# Copyright 2022-present, the HuggingFace Inc. team.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Contains utilities to manage the HF cache directory."""

import os
import shutil
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from huggingface_hub.errors import CacheNotFound, CorruptedCacheException

from ..constants import HF_HUB_CACHE, REPO_TYPES_MAPPING
from . import _shared_blobs, logging
from ._parsing import format_timesince
from ._terminal import tabulate


logger = logging.get_logger(__name__)

REPO_TYPE_T = Literal["model", "dataset", "space", "kernel"]

# List of OS-created helper files that need to be ignored
FILES_TO_IGNORE = [".DS_Store", "Thumbs.db", "desktop.ini"]


@dataclass(frozen=True)
class CachedFileInfo:
    """Frozen data structure holding information about a single cached file.

    Args:
        file_name (`str`):
            Name of the file. Example: `config.json`.
        file_path (`Path`):
            Path of the file in the `snapshots` directory. The file path is a symlink
            referring to a blob in the `blobs` folder.
        blob_path (`Path`):
            Path of the repo-local blob entry referenced by `file_path`. The blob entry
            can itself be a symlink to the cache-wide shared blob store.
        size_on_disk (`int`):
            Size of the blob file in bytes.
        blob_last_accessed (`float`):
            Timestamp of the last time the blob file has been accessed (from any
            revision).
        blob_last_modified (`float`):
            Timestamp of the last time the blob file has been modified/created.

    > [!WARNING]
    > `blob_last_accessed` and `blob_last_modified` reliability can depend on the OS you
    > are using. See [python documentation](https://docs.python.org/3/library/os.html#os.stat_result)
    > for more details.
    """

    file_name: str
    file_path: Path
    blob_path: Path
    size_on_disk: int

    blob_last_accessed: float
    blob_last_modified: float

    @property
    def blob_last_accessed_str(self) -> str:
        """
        (property) Timestamp of the last time the blob file has been accessed (from any
        revision), returned as a human-readable string.

        Example: "2 weeks ago".
        """
        return format_timesince(self.blob_last_accessed)

    @property
    def blob_last_modified_str(self) -> str:
        """
        (property) Timestamp of the last time the blob file has been modified, returned
        as a human-readable string.

        Example: "2 weeks ago".
        """
        return format_timesince(self.blob_last_modified)

    @property
    def size_on_disk_str(self) -> str:
        """
        (property) Size of the blob file as a human-readable string.

        Example: "42.2K".
        """
        return _format_size(self.size_on_disk)


@dataclass(frozen=True)
class CachedRevisionInfo:
    """Frozen data structure holding information about a revision.

    A revision correspond to a folder in the `snapshots` folder and is populated with
    the exact tree structure as the repo on the Hub but contains only symlinks. A
    revision can be either referenced by 1 or more `refs` or be "detached" (no refs).

    Args:
        commit_hash (`str`):
            Hash of the revision (unique).
            Example: `"9338f7b671827df886678df2bdd7cc7b4f36dffd"`.
        snapshot_path (`Path`):
            Path to the revision directory in the `snapshots` folder. It contains the
            exact tree structure as the repo on the Hub.
        files: (`frozenset[CachedFileInfo]`):
            Set of [`~CachedFileInfo`] describing all files contained in the snapshot.
        refs (`frozenset[str]`):
            Set of `refs` pointing to this revision. If the revision has no `refs`, it
            is considered detached.
            Example: `{"main", "2.4.0"}` or `{"refs/pr/1"}`.
        size_on_disk (`int`):
            Sum of the blob file sizes that are symlink-ed by the revision.
        last_modified (`float`):
            Timestamp of the last time the revision has been created/modified.

    > [!WARNING]
    > `last_accessed` cannot be determined correctly on a single revision as blob files
    > are shared across revisions.

    > [!WARNING]
    > `size_on_disk` is not necessarily the sum of all file sizes because of possible
    > duplicated files. Besides, only blobs are taken into account, not the (negligible)
    > size of folders and symlinks.
    """

    commit_hash: str
    snapshot_path: Path
    size_on_disk: int
    files: frozenset[CachedFileInfo]
    refs: frozenset[str]

    last_modified: float

    @property
    def last_modified_str(self) -> str:
        """
        (property) Timestamp of the last time the revision has been modified, returned
        as a human-readable string.

        Example: "2 weeks ago".
        """
        return format_timesince(self.last_modified)

    @property
    def size_on_disk_str(self) -> str:
        """
        (property) Sum of the blob file sizes as a human-readable string.

        Example: "42.2K".
        """
        return _format_size(self.size_on_disk)

    @property
    def nb_files(self) -> int:
        """
        (property) Total number of files in the revision.
        """
        return len(self.files)


@dataclass(frozen=True)
class CachedRepoInfo:
    """Frozen data structure holding information about a cached repository.

    Args:
        repo_id (`str`):
            Repo id of the repo on the Hub. Example: `"google/fleurs"`.
        repo_type (`Literal["dataset", "model", "space", "kernel"]`):
            Type of the cached repo.
        repo_path (`Path`):
            Local path to the cached repo.
        size_on_disk (`int`):
            Sum of the blob file sizes in the cached repo.
        nb_files (`int`):
            Total number of blob files in the cached repo.
        revisions (`frozenset[CachedRevisionInfo]`):
            Set of [`~CachedRevisionInfo`] describing all revisions cached in the repo.
        last_accessed (`float`):
            Timestamp of the last time a blob file of the repo has been accessed.
        last_modified (`float`):
            Timestamp of the last time a blob file of the repo has been modified/created.

    > [!WARNING]
    > `size_on_disk` is not necessarily the sum of all revisions sizes because of
    > duplicated files. Besides, only blobs are taken into account, not the (negligible)
    > size of folders and symlinks.

    > [!WARNING]
    > `last_accessed` and `last_modified` reliability can depend on the OS you are using.
    > See [python documentation](https://docs.python.org/3/library/os.html#os.stat_result)
    > for more details.
    """

    repo_id: str
    repo_type: REPO_TYPE_T
    repo_path: Path
    size_on_disk: int
    nb_files: int
    revisions: frozenset[CachedRevisionInfo]

    last_accessed: float
    last_modified: float

    @property
    def last_accessed_str(self) -> str:
        """
        (property) Last time a blob file of the repo has been accessed, returned as a
        human-readable string.

        Example: "2 weeks ago".
        """
        return format_timesince(self.last_accessed)

    @property
    def last_modified_str(self) -> str:
        """
        (property) Last time a blob file of the repo has been modified, returned as a
        human-readable string.

        Example: "2 weeks ago".
        """
        return format_timesince(self.last_modified)

    @property
    def size_on_disk_str(self) -> str:
        """
        (property) Sum of the blob file sizes as a human-readable string.

        Example: "42.2K".
        """
        return _format_size(self.size_on_disk)

    @property
    def cache_id(self) -> str:
        """Canonical `type/id` identifier used across cache tooling."""
        return f"{self.repo_type}/{self.repo_id}"

    @property
    def refs(self) -> dict[str, CachedRevisionInfo]:
        """
        (property) Mapping between `refs` and revision data structures.
        """
        return {ref: revision for revision in self.revisions for ref in revision.refs}


@dataclass(frozen=True)
class DeleteCacheStrategy:
    """Frozen data structure holding the strategy to delete cached revisions or files.

    This object is not meant to be instantiated programmatically but to be returned by
    [`~utils.HFCacheInfo.delete_revisions`] or [`~utils.HFCacheInfo.delete_files`]. See
    documentation for usage example.

    Args:
        expected_freed_size (`float`):
            Expected freed size once strategy is executed.
        blobs (`frozenset[Path]`):
            Set of blob file paths to be deleted.
        refs (`frozenset[Path]`):
            Set of reference file paths to be deleted.
        repos (`frozenset[Path]`):
            Set of entire repo paths to be deleted.
        snapshots (`frozenset[Path]`):
            Set of snapshots to be deleted (directory of symlinks).
        files (`frozenset[Path]`, *optional*):
            Set of individual snapshot entries to be deleted. Their blobs are deleted only if
            listed in `blobs`.
        cache_dir (`Path` or `None`):
            Cache directory the strategy was computed from. Used to collect shared
            blobs referenced by repo-local symlinks removed by the deletion.
    """

    expected_freed_size: int
    blobs: frozenset[Path]
    refs: frozenset[Path]
    repos: frozenset[Path]
    snapshots: frozenset[Path]
    files: frozenset[Path] = frozenset()
    cache_dir: Path | None = None

    @property
    def expected_freed_size_str(self) -> str:
        """
        (property) Expected size that will be freed as a human-readable string.

        Example: "42.2K".
        """
        return _format_size(self.expected_freed_size)

    def execute(self) -> None:
        """Execute the defined strategy.

        > [!WARNING]
        > If this method is interrupted, the cache might get corrupted. Deletion order is
        > implemented so that references and symlinks are deleted before the actual blob
        > files.

        > [!WARNING]
        > This method is irreversible. If executed, cached files are erased and must be
        > downloaded again.
        """
        # Record shared targets before their repo symlinks are deleted; only those manifests are swept below.
        shared_store_paths: set[Path] = set()
        if self.cache_dir is not None and _shared_blobs.is_shared_blobs_dir(
            _shared_blobs.shared_blobs_dir(self.cache_dir)
        ):
            blob_paths = set(self.blobs)
            for repo_path in self.repos:
                blobs_dir = repo_path / "blobs"
                try:
                    blob_paths.update(blobs_dir.iterdir())
                except OSError:
                    pass
            for blob_path in blob_paths:
                if store_path := _shared_blobs.shared_blob_target(blob_path, self.cache_dir):
                    shared_store_paths.add(store_path)

        # Deletion order matters. Blobs are deleted in last so that the user can't end
        # up in a state where a `ref`` refers to a missing snapshot or a snapshot
        # symlink refers to a deleted blob.

        # Unlink snapshot entries first: if it fails, their blobs must not be deleted.
        for path in self.files:
            path.unlink(missing_ok=True)

        # Delete entire repos
        for path in self.repos:
            _try_delete_path(path, path_type="repo")

        # Delete snapshot directories
        for path in self.snapshots:
            _try_delete_path(path, path_type="snapshot")

        # Delete refs files
        for path in self.refs:
            _try_delete_path(path, path_type="ref")

        # Delete blob files
        for path in self.blobs:
            _try_delete_path(path, path_type="blob")

        # Manifest entries are validated against the filesystem; failures leak data rather than delete it.
        if self.cache_dir is not None:
            for store_path in shared_store_paths:
                _shared_blobs.sweep_shared_blob(store_path, cache_dir=self.cache_dir)

        logger.info(f"Cache deletion done. Saved {self.expected_freed_size_str}.")


@dataclass(frozen=True)
class CachedIncompleteFileInfo:
    """Frozen data structure holding information about a single incomplete download.

    Interrupted downloads leave `<cache>/<repo>/blobs/<etag>.incomplete` files behind.
    These are not part of any committed revision, so they are surfaced separately by
    [`scan_cache_dir`].

    Args:
        file_path (`Path`):
            Path of the `.incomplete` file in the `blobs` folder.
        size_on_disk (`int`):
            Size of the partially-downloaded file in bytes.
    """

    file_path: Path
    size_on_disk: int


@dataclass(frozen=True)
class HFCacheInfo:
    """Frozen data structure holding information about the entire cache-system.

    This data structure is returned by [`scan_cache_dir`] and is immutable.

    Args:
        size_on_disk (`int`):
            Physical size of the cache-system in bytes. A blob shared across repos is
            counted once, and store-only shared blobs no longer referenced by any repo are
            included. Can be smaller than the sum of the per-repo sizes.
        repos (`frozenset[CachedRepoInfo]`):
            Set of [`~CachedRepoInfo`] describing all valid cached repos found on the
            cache-system while scanning.
        incomplete_files (`frozenset[CachedIncompleteFileInfo]`):
            Set of [`~CachedIncompleteFileInfo`] describing orphaned `*.incomplete`
            files left behind by interrupted downloads.
        warnings (`list[CorruptedCacheException]`):
            List of [`~CorruptedCacheException`] that occurred while scanning the cache.
            Those exceptions are captured so that the scan can continue. Corrupted repos
            are skipped from the scan.
        cache_dir (`Path` or `None`):
            Cache directory that was scanned.

    > [!WARNING]
    > `size_on_disk` only accounts for blobs, and corrupted repos are skipped. It is a
    > physical size: unlike `CachedRepoInfo.size_on_disk`, which attributes a shared blob
    > to every repo referencing it, a shared blob is counted here only once.
    """

    size_on_disk: int
    repos: frozenset[CachedRepoInfo]
    incomplete_files: frozenset[CachedIncompleteFileInfo]
    warnings: list[CorruptedCacheException]
    cache_dir: Path | None = None

    @property
    def size_on_disk_str(self) -> str:
        """
        (property) Physical size of the cache-system as a human-readable string.

        Example: "42.2K".
        """
        return _format_size(self.size_on_disk)

    @property
    def incomplete_size_on_disk(self) -> int:
        """(property) Sum of all incomplete download sizes in bytes."""
        return sum(file.size_on_disk for file in self.incomplete_files)

    def delete_revisions(self, *revisions: str) -> DeleteCacheStrategy:
        """Prepare the strategy to delete one or more revisions cached locally.

        Input revisions can be any revision hash. If a revision hash is not found in the
        local cache, a warning is thrown but no error is raised. Revisions can be from
        different cached repos since hashes are unique across repos,

        Examples:
        ```py
        >>> from huggingface_hub import scan_cache_dir
        >>> cache_info = scan_cache_dir()
        >>> delete_strategy = cache_info.delete_revisions(
        ...     "81fd1d6e7847c99f5862c9fb81387956d99ec7aa"
        ... )
        >>> print(f"Will free {delete_strategy.expected_freed_size_str}.")
        Will free 7.9K.
        >>> delete_strategy.execute()
        Cache deletion done. Saved 7.9K.
        ```

        ```py
