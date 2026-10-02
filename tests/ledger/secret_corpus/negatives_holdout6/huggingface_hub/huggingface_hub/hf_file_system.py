import os
import tempfile
import threading
from collections import deque
from collections.abc import Iterable, Iterator
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from itertools import chain
from pathlib import PurePosixPath
from typing import Any, NoReturn, Union
from urllib.parse import quote, unquote

import fsspec
import httpx
from fsspec.callbacks import _DEFAULT_CALLBACK, NoOpCallback, TqdmCallback
from fsspec.config import apply_config
from fsspec.utils import isfilelike

from . import constants
from ._buckets import BucketFile, BucketFolder
from ._commit_api import CommitOperationCopy, CommitOperationDelete
from ._local_folder import _validate_relative_filename
from .errors import (
    BucketNotFoundError,
    EntryNotFoundError,
    HfHubHTTPError,
    RepositoryNotFoundError,
    RevisionNotFoundError,
)
from .file_download import hf_hub_url, http_get
from .hf_api import SPECIAL_REFS_REVISION_REGEX, HfApi, LastCommitInfo, RepoFile, RepoFolder
from .utils import HFValidationError, hf_raise_for_status, http_backoff, http_stream_backoff, parse_hf_uri
from .utils.insecure_hashlib import md5


@dataclass
class HfFileSystemResolvedPath:
    """Top level Data structure containing information about a resolved Hugging Face file system path."""

    root: str
    path: str

    def unresolve(self) -> str:
        return f"{self.root}/{self.path}".rstrip("/")


@dataclass
class HfFileSystemResolvedRepositoryPath(HfFileSystemResolvedPath):
    """Data structure containing information about a resolved path in a repository."""

    repo_type: str
    repo_id: str
    revision: str
    path_in_repo: str
    root: str = field(init=False)
    path: str = field(init=False)
    # The part placed after '@' in the initial path. It can be a quoted or unquoted refs revision.
    # Used to reconstruct the unresolved path to return to the user.
    _raw_revision: str | None = field(default=None, repr=False)

    def __post_init__(self):
        repo_path = constants.REPO_TYPES_URL_PREFIXES.get(self.repo_type, "") + self.repo_id
        if self._raw_revision:
            self.root = f"{repo_path}@{self._raw_revision}"
        elif self.revision != constants.DEFAULT_REVISION:
            self.root = f"{repo_path}@{safe_revision(self.revision)}"
        else:
            self.root = repo_path
        self.path = self.path_in_repo


@dataclass
class HfFileSystemResolvedBucketPath(HfFileSystemResolvedPath):
    """Data structure containing information about a resolved path in a bucket."""

    bucket_id: str
    root: str = field(init=False)

    def __post_init__(self):
        self.root = "buckets/" + self.bucket_id


# We need to improve fsspec.spec._Cached which is AbstractFileSystem's metaclass
_cached_base: Any = type(fsspec.AbstractFileSystem)


class _Cached(_cached_base):
    """
    Metaclass for caching HfFileSystem instances according to the args.

    This creates an additional reference to the filesystem, which prevents the
    filesystem from being garbage collected when all *user* references go away.
    A call to the :meth:`AbstractFileSystem.clear_instance_cache` must *also*
    be made for a filesystem instance to be garbage collected.

    This is a slightly modified version of `fsspec.spec._Cached` to improve it.
    In particular in `_tokenize` the pid isn't taken into account for the
    `fs_token` used to identify cached instances. The `fs_token` logic is also
    robust to defaults values and the order of the args. Finally new instances
    reuse the states from sister instances in the main thread.
    """

    def __init__(cls, *args, **kwargs):
        # Hack: override https://github.com/fsspec/filesystem_spec/blob/dcb167e8f50e6273d4cfdfc4cab8fc5aa4c958bf/fsspec/spec.py#L53
        super().__init__(*args, **kwargs)
        # Note: we intentionally create a reference here, to avoid garbage
        # collecting instances when all other references are gone. To really
        # delete a FileSystem, the cache must be cleared.
        cls._cache = {}

    def __call__(cls, *args, **kwargs):
        # Hack: override https://github.com/fsspec/filesystem_spec/blob/dcb167e8f50e6273d4cfdfc4cab8fc5aa4c958bf/fsspec/spec.py#L65
        # Apply fsspec config (env vars / config files) before tokenizing so that
        # HfFileSystem picks up defaults the same way other fsspec filesystems do.
        kwargs = apply_config(cls, kwargs)
        skip = kwargs.pop("skip_instance_cache", False)
        fs_token = cls._tokenize(cls, threading.get_ident(), *args, **kwargs)
        fs_token_main_thread = cls._tokenize(cls, threading.main_thread().ident, *args, **kwargs)
        if not skip and cls.cachable and fs_token in cls._cache:
            # reuse cached instance
            cls._latest = fs_token
            return cls._cache[fs_token]
        else:
            # create new instance
            obj = type.__call__(cls, *args, **kwargs)
            if not skip and cls.cachable and fs_token_main_thread in cls._cache:
                # reuse the cache from the main thread instance in the new instance
                instance_state = cls._cache[fs_token_main_thread]._get_instance_state()
                for attr, state_value in instance_state.items():
                    setattr(obj, attr, state_value)
            obj._fs_token_ = fs_token
            obj.storage_args = args
            obj.storage_options = kwargs
            if cls.cachable and not skip:
                cls._latest = fs_token
                cls._cache[fs_token] = obj
            return obj


class HfFileSystem(fsspec.AbstractFileSystem, metaclass=_Cached):  # ty: ignore[conflicting-metaclass]
    """
    Access a remote Hugging Face Hub repository as if were a local file system.

    > [!WARNING]
    > [`HfFileSystem`] provides fsspec compatibility, which is useful for libraries that require it (e.g., reading
    >     Hugging Face datasets directly with `pandas`). However, it introduces additional overhead due to this compatibility
    >     layer. For better performance and reliability, it's recommended to use `HfApi` methods when possible.

    The file system supports paths for the `hf://` protocol, which follows those URL schemes:

    * Models, Datasets and Spaces repositories:

        ```
        hf://<repo-id>[@<revision>]/<path/in/repo>
        hf://datasets/<repo-id>[@<revision>]/<path/in/repo>
        hf://spaces/<repo-id>[@<revision>]/<path/in/repo>
        ```

    * Buckets (generic storage):

        ```
        hf://buckets/<bucket-id>/<path/in/bucket>
        ```

    Note: when using the [`HfFileSystem`] directly, passing the `hf://` protocol prefix is optional in paths.

    Args:
        endpoint (`str`, *optional*):
                Endpoint of the Hub. Defaults to <https://huggingface.co>.
        token (`bool` or `str`, *optional*):
            A valid user access token (string). Defaults to the locally saved
            token, which is the recommended method for authentication (see
            https://huggingface.co/docs/huggingface_hub/quick-start#authentication).
            To disable authentication, pass `False`.
        block_size (`int`, *optional*):
            Block size for reading and writing files.
        expand_info (`bool`, *optional*):
            Whether to expand the information of the files.
        **storage_options (`dict`, *optional*):
            Additional options for the filesystem. See [fsspec documentation](https://filesystem-spec.readthedocs.io/en/latest/api.html#fsspec.spec.AbstractFileSystem.__init__).

    Usage:

    ```python
    >>> from huggingface_hub import hffs

    >>> # List files
    >>> hffs.glob("my-username/my-model/*.bin")
    ['my-username/my-model/pytorch_model.bin']
    >>> hffs.ls("datasets/my-username/my-dataset", detail=False)
    ['datasets/my-username/my-dataset/.gitattributes', 'datasets/my-username/my-dataset/README.md', 'datasets/my-username/my-dataset/data.json']

    >>> # Read/write files
    >>> with hffs.open("my-username/my-model/pytorch_model.bin") as f:
    ...     data = f.read()
    >>> with hffs.open("my-username/my-model/pytorch_model.bin", "wb") as f:
    ...     f.write(data)
    ```

    Specify a token for authentication:
    ```python
    >>> from huggingface_hub import HfFileSystem
    >>> hffs = HfFileSystem(token=token)
    ```
    """

    root_marker = ""
    protocol = "hf"

    def __init__(
        self,
        *args,
        endpoint: str | None = None,
        token: bool | str | None = None,
        block_size: int | None = None,
        expand_info: bool | None = None,
        **storage_options,
    ):
        super().__init__(*args, **storage_options)
        self.endpoint = endpoint or constants.ENDPOINT
        self.token = token
        self._api = HfApi(endpoint=endpoint, token=token)
        self.block_size = block_size
        self.expand_info = expand_info
        # Maps (repo_type, repo_id, revision) to a 2-tuple with:
        #  * the 1st element indicating whether the repository and the revision exist
        #  * the 2nd element being the exception raised if the repository or revision doesn't exist
        self._repo_and_revision_exists_cache: dict[tuple[str, str, str | None], tuple[bool, Exception | None]] = {}
        # Same for buckets
        self._bucket_exists_cache: dict[str, tuple[bool, Exception | None]] = {}
        # Note: special case for buckets: revision is always None
        # Maps parent directory path to path infos
        self.dircache: dict[str, list[dict[str, Any]]] = {}

    @classmethod
    def _tokenize(cls, threading_ident: int, *args, **kwargs) -> str:
        """Deterministic token for caching"""
        # make fs_token robust to default values and to kwargs order
        kwargs["endpoint"] = kwargs.get("endpoint") or constants.ENDPOINT
        kwargs["token"] = kwargs.get("token")
        kwargs = {key: kwargs[key] for key in sorted(kwargs)}
        # contrary to fsspec, we don't include pid here
        tokenize_args = (cls, threading_ident, args, kwargs)
        h = md5(str(tokenize_args).encode())
        return h.hexdigest()

    def _repo_and_revision_exist(
        self, repo_type: str, repo_id: str, revision: str | None
    ) -> tuple[bool, Exception | None]:
        if (repo_type, repo_id, revision) not in self._repo_and_revision_exists_cache:
            try:
                self._api.repo_info(
                    repo_id, revision=revision, repo_type=repo_type, timeout=constants.HF_HUB_ETAG_TIMEOUT
                )
            except (RepositoryNotFoundError, HFValidationError) as e:
                self._repo_and_revision_exists_cache[(repo_type, repo_id, revision)] = False, e
                self._repo_and_revision_exists_cache[(repo_type, repo_id, None)] = False, e
            except RevisionNotFoundError as e:
                self._repo_and_revision_exists_cache[(repo_type, repo_id, revision)] = False, e
                self._repo_and_revision_exists_cache[(repo_type, repo_id, None)] = True, None
            else:
                self._repo_and_revision_exists_cache[(repo_type, repo_id, revision)] = True, None
                self._repo_and_revision_exists_cache[(repo_type, repo_id, None)] = True, None
        return self._repo_and_revision_exists_cache[(repo_type, repo_id, revision)]

    def _bucket_exists(self, bucket_id: str) -> tuple[bool, Exception | None]:
        if bucket_id not in self._bucket_exists_cache:
            try:
                self._api.bucket_info(bucket_id)
            except BucketNotFoundError as e:
                self._bucket_exists_cache[bucket_id] = False, e
            else:
                self._bucket_exists_cache[bucket_id] = True, None
        return self._bucket_exists_cache[bucket_id]

    def resolve_path(
        self, path: str, revision: str | None = None
    ) -> HfFileSystemResolvedRepositoryPath | HfFileSystemResolvedBucketPath:
        """
        Resolve a Hugging Face file system path into its components.

        Args:
            path (`str`):
                Path to resolve.
            revision (`str`, *optional*):
                The revision of the repo to resolve. Defaults to the revision specified in the path.

        Returns:
            [`HfFileSystemResolvedPath`]: Resolved path information containing `repo_type`, `repo_id`, `revision` and `path_in_repo`.

        Raises:
            `ValueError`:
                If path contains conflicting revision information.
            `NotImplementedError`:
                If trying to list repositories.
        """
        path = self._strip_protocol(path)
        if not path:
            raise NotImplementedError("Access to buckets and repositories lists is not implemented.")
        if path.count("/") == 0:
            raise ValueError(
                f"Repository id must be 'namespace/name', got '{path}'. Single-segment ids (e.g. 'gpt2') are no longer supported."
            )

        parsed = parse_hf_uri(f"{constants.HF_PROTOCOL}{path}")

        # --- Buckets ---
        if parsed.is_bucket:
            bucket_exists, err = self._bucket_exists(parsed.id)
            if not bucket_exists:
                _raise_file_not_found(path, err)
            return HfFileSystemResolvedBucketPath(bucket_id=parsed.id, path=parsed.path_in_repo)

        # --- Repositories ---
        # Align revision from path with explicit revision argument
        if revision is not None and parsed.revision is not None and parsed.revision != revision:
            # The caller provided an explicit revision that conflicts with what parse_hf_uri
            # parsed. This can happen when a user has a branch literally named "refs" and a
            # file at "pr/10" — parse_hf_uri would greedily match "refs/pr/10" as a special
            # ref. Fall back to simple '@' splitting so the caller's revision wins.
            path_without_type = path.split("/", 1)[1] if path.split("/")[0] in constants.HF_URI_TYPE_PREFIXES else path
            repo_id, after_at = path_without_type.split("@", 1)
            revision_in_path, path_in_repo = after_at.split("/", 1) if "/" in after_at else (after_at, "")
            revision_in_path_decoded = unquote(revision_in_path)
            if revision_in_path_decoded != revision:
                raise ValueError(
                    f'Revision specified in path ("{revision_in_path_decoded}") and in `revision` argument ("{revision}") are not the same.'
                )
            repo_and_revision_exist, err = self._repo_and_revision_exist(parsed.type, repo_id, revision)
            if not repo_and_revision_exist:
                _raise_file_not_found(path, err)
            return HfFileSystemResolvedRepositoryPath(
                parsed.type, repo_id, revision, path_in_repo, _raw_revision=revision_in_path
            )

        if parsed.revision is not None and revision is None:
            revision = parsed.revision

        repo_and_revision_exist, err = self._repo_and_revision_exist(parsed.type, parsed.id, revision)
        if not repo_and_revision_exist:
            _raise_file_not_found(path, err)

        # Extract raw revision from original path for unresolve() fidelity
        raw_revision: str | None = None
        if "@" in path and parsed.revision is not None:
            path_without_type = path.split("/", 1)[1] if path.split("/")[0] in constants.HF_URI_TYPE_PREFIXES else path
            raw_after_at = path_without_type.split("@", 1)[1]
            raw_revision = raw_after_at[: -(len(parsed.path_in_repo) + 1)] if parsed.path_in_repo else raw_after_at

        revision = revision if revision is not None else constants.DEFAULT_REVISION
        return HfFileSystemResolvedRepositoryPath(
            parsed.type, parsed.id, revision, parsed.path_in_repo, _raw_revision=raw_revision
        )

    def invalidate_cache(self, path: str | None = None) -> None:
        """
        Clear the cache for a given path.

        For more details, refer to [fsspec documentation](https://filesystem-spec.readthedocs.io/en/latest/api.html#fsspec.spec.AbstractFileSystem.invalidate_cache).

        Args:
            path (`str`, *optional*):
                Path to clear from cache. If not provided, clear the entire cache.

        """
        if not path:
