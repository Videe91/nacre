from __future__ import annotations

import io
import json
import logging
import os
import threading
import warnings
import weakref
from errno import ESPIPE
from glob import has_magic
from hashlib import sha256
from typing import Any

from .callbacks import DEFAULT_CALLBACK
from .config import apply_config, conf
from .dircache import DirCache
from .transaction import Transaction
from .utils import (
    _unstrip_protocol,
    check_contained,
    glob_translate,
    isfilelike,
    other_paths,
    read_block,
    stringify_path,
    tokenize,
)

logger = logging.getLogger("fsspec")


def make_instance(cls, args, kwargs):
    return cls(*args, **kwargs)


FORK_AVAILABLE = hasattr(os, "register_at_fork")


if FORK_AVAILABLE:
    _registered_classes = weakref.WeakSet()

    def _reset_instances_lock():
        for cls in _registered_classes:
            cls._instantiation_lock = threading.RLock()
            cls._cache.clear()
            cls._pid = os.getpid()

    os.register_at_fork(after_in_child=_reset_instances_lock)


class _Cached(type):
    """
    Metaclass for caching file system instances.

    Notes
    -----
    Instances are cached according to

    * The values of the class attributes listed in `_extra_tokenize_attributes`
    * The arguments passed to ``__init__``.

    This creates an additional reference to the filesystem, which prevents the
    filesystem from being garbage collected when all *user* references go away.
    A call to the :meth:`AbstractFileSystem.clear_instance_cache` must *also*
    be made for a filesystem instance to be garbage collected.
    """

    def __init__(cls, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Note: we intentionally create a reference here, to avoid garbage
        # collecting instances when all other references are gone. To really
        # delete a FileSystem, the cache must be cleared.
        if conf.get("weakref_instance_cache"):  # pragma: no cover
            # debug option for analysing fork/spawn conditions
            cls._cache = weakref.WeakValueDictionary()
        else:
            cls._cache = {}
        cls._pid = os.getpid()
        cls._instantiation_lock = threading.RLock()

        if FORK_AVAILABLE:
            _registered_classes.add(cls)

    def _check_instance_cache(cls, token):
        inst = cls._cache.get(token)
        if inst is not None:
            cls._latest = token
        return inst

    def __call__(cls, *args, **kwargs):
        kwargs = apply_config(cls, kwargs)
        extra_tokens = tuple(
            getattr(cls, attr, None) for attr in cls._extra_tokenize_attributes
        )
        strip_tokenize_options = {
            k: kwargs.pop(k) for k in cls._strip_tokenize_options if k in kwargs
        }
        pid = os.getpid()

        if getattr(cls, "async_impl", False) and not kwargs.get("asynchronous", False):
            token = tokenize(cls, pid, *args, *extra_tokens, **kwargs)
        else:
            token = tokenize(
                cls, pid, threading.get_ident(), *args, *extra_tokens, **kwargs
            )
        skip = kwargs.pop("skip_instance_cache", False)

        if pid != cls._pid:
            with cls._instantiation_lock:
                if pid != cls._pid:
                    cls._cache.clear()
                    cls._pid = pid

        if not skip and cls.cachable:
            inst = cls._check_instance_cache(token)
            if inst is not None:
                return inst

            with cls._instantiation_lock:
                # protect against the race condition that a new instance was created
                # and inserted into the cache since the initial check just above
                inst = cls._check_instance_cache(token)
                if inst is not None:
                    return inst

        obj = super().__call__(*args, **kwargs, **strip_tokenize_options)
        # Setting _fs_token here causes some static linters to complain.
        obj._fs_token_ = token
        obj.storage_args = args
        obj.storage_options = kwargs
        if obj.async_impl and obj.mirror_sync_methods:
            from .asyn import mirror_sync_methods

            mirror_sync_methods(obj)

        if cls.cachable and not skip:
            with cls._instantiation_lock:
                # another thread may have created the instance while we were calling
                # super().__call__(), so we check again.
                inst = cls._check_instance_cache(token)
                if inst is not None:
                    return inst

                cls._latest = token
                cls._cache[token] = obj
        return obj


class AbstractFileSystem(metaclass=_Cached):
    """
    An abstract super-class for pythonic file-systems

    Implementations are expected to be compatible with or, better, subclass
    from here.
    """

    cachable = True  # this class can be cached, instances reused
    _cached = False
    blocksize = 2**22
    sep = "/"
    # Implementations may select their protocol per instance (for example, a
    # single adapter class backed by different storage implementations).
    protocol: str | tuple[str, ...] = "abstract"
    _latest = None
    async_impl = False
    mirror_sync_methods = False
    root_marker = ""  # For some FSs, may require leading '/' or other character
    transaction_type = Transaction

    #: Extra *class attributes* that should be considered when hashing.
    _extra_tokenize_attributes = ()
    #: *storage options* that should not be considered when hashing.
    _strip_tokenize_options = ()

    # Set by _Cached metaclass
    storage_args: tuple[Any, ...]
    storage_options: dict[str, Any]

    def __init__(self, *args, **storage_options):
        """Create and configure file-system instance

        Instances may be cachable, so if similar enough arguments are seen
        a new instance is not required. The token attribute exists to allow
        implementations to cache instances if they wish.

        A reasonable default should be provided if there are no arguments.

        Subclasses should call this method.

        Parameters
        ----------
        use_listings_cache, listings_expiry_time, max_paths:
            passed to ``DirCache``, if the implementation supports
            directory listing caching. Pass use_listings_cache=False
            to disable such caching.
        skip_instance_cache: bool
            If this is a cachable implementation, pass True here to force
            creating a new instance even if a matching instance exists, and prevent
            storing this instance.
        asynchronous: bool
        loop: asyncio-compatible IOLoop or None
        """
        if self._cached:
            # reusing instance, don't change
            return
        self._cached = True
        self._intrans = False
        self._transaction = None
        self._invalidated_caches_in_transaction = []
        self.dircache = DirCache(**storage_options)

        if storage_options.pop("add_docs", None):
            warnings.warn("add_docs is no longer supported.", FutureWarning)

        if storage_options.pop("add_aliases", None):
            warnings.warn("add_aliases has been removed.", FutureWarning)
        # This is set in _Cached
        self._fs_token_ = None

    @property
    def fsid(self):
        """Persistent filesystem id that can be used to compare filesystems
        across sessions.
        """
        raise NotImplementedError

    @property
    def _fs_token(self):
        return self._fs_token_

    def __dask_tokenize__(self):
        return self._fs_token

    def __hash__(self):
        return int(self._fs_token, 16)

    def __eq__(self, other):
        return isinstance(other, type(self)) and self._fs_token == other._fs_token

    def __reduce__(self):
        return make_instance, (type(self), self.storage_args, self.storage_options)

    @classmethod
    def _strip_protocol(cls, path):
        """Turn path from fully-qualified to file-system-specific

        May require FS-specific handling, e.g., for relative paths or links.
        """
        if isinstance(path, list):
            return [cls._strip_protocol(p) for p in path]
        path = stringify_path(path)
        protos = (cls.protocol,) if isinstance(cls.protocol, str) else cls.protocol
        for protocol in protos:
            if path.startswith(protocol + "://"):
                path = path[len(protocol) + 3 :]
            elif path.startswith(protocol + "::"):
                path = path[len(protocol) + 2 :]
        path = path.rstrip("/")
        # use of root_marker to make minimum required path, e.g., "/"
        return path or cls.root_marker

    def unstrip_protocol(self, name: str) -> str:
        """Format FS-specific path to generic, including protocol"""
        protos = (self.protocol,) if isinstance(self.protocol, str) else self.protocol
        for protocol in protos:
            if name.startswith(f"{protocol}://"):
                return name
        return f"{protos[0]}://{name}"

    @staticmethod
    def _get_kwargs_from_urls(path):
        """If kwargs can be encoded in the paths, extract them here

        This should happen before instantiation of the class; incoming paths
        then should be amended to strip the options in methods.

        Examples may look like an sftp path "sftp://user@host:/my/path", where
        the user and host should become kwargs and later get stripped.
        """
        # by default, nothing happens
        return {}

    @classmethod
    def current(cls):
        """Return the most recently instantiated FileSystem

        If no instance has been created, then create one with defaults
        """
        inst = cls._cache.get(cls._latest)
        if inst is not None:
            return inst
        return cls()

    @property
    def transaction(self):
        """A context within which files are committed together upon exit

        Requires the file class to implement `.commit()` and `.discard()`
        for the normal and exception cases.
        """
        if self._transaction is None:
            self._transaction = self.transaction_type(self)
        return self._transaction

    def start_transaction(self):
        """Begin write transaction for deferring files, non-context version"""
        self._intrans = True
        self._transaction = self.transaction_type(self)
        return self.transaction

    def end_transaction(self):
        """Finish write transaction, non-context version"""
        self.transaction.complete()
        self._transaction = None
        # The invalid cache must be cleared after the transaction is completed.
        for path in self._invalidated_caches_in_transaction:
            self.invalidate_cache(path)
        self._invalidated_caches_in_transaction.clear()

    def invalidate_cache(self, path=None):
        """
        Discard any cached directory information

        Parameters
        ----------
        path: string or None
            If None, clear all listings cached else listings at or under given
            path.
        """
        # Not necessary to implement invalidation mechanism, may have no cache.
        # But if have, you should call this method of parent class from your
        # subclass to ensure expiring caches after transacations correctly.
        # See the implementation of FTPFileSystem in ftp.py
        if self._intrans:
            self._invalidated_caches_in_transaction.append(path)

    def mkdir(self, path, create_parents=True, **kwargs):
        """
        Create directory entry at path

        For systems that don't have true directories, may create an for
        this instance only and not touch the real filesystem

        Parameters
        ----------
        path: str
            location
        create_parents: bool
            if True, this is equivalent to ``makedirs``
        kwargs:
            may be permissions, etc.
        """
        pass  # not necessary to implement, may not have directories

    def makedirs(self, path, exist_ok=False):
        """Recursively make directories

        Creates directory at path and any intervening required directories.
        Raises exception if, for instance, the path already exists but is a
        file.

        Parameters
        ----------
        path: str
            leaf directory name
        exist_ok: bool (False)
            If False, will error if the target already exists
        """
        pass  # not necessary to implement, may not have directories

    def rmdir(self, path):
        """Remove a directory, if empty"""
        pass  # not necessary to implement, may not have directories

    def ls(self, path, detail=True, **kwargs):
        """List objects at path.

        This should include subdirectories and files at that location. The
        difference between a file and a directory must be clear when details
        are requested.

        The specific keys, or perhaps a FileInfo class, or similar, is TBD,
        but must be consistent across implementations.
        Must include:

        - full path to the entry (without protocol)
        - size of the entry, in bytes. If the value cannot be determined, will
          be ``None``.
        - type of entry, "file", "directory" or other

        Additional information
        may be present, appropriate to the file-system, e.g., generation,
        checksum, etc.

        May use refresh=True|False to allow use of self._ls_from_cache to
        check for a saved listing and avoid calling the backend. This would be
        common where listing may be expensive.

        Parameters
        ----------
        path: str
        detail: bool
            if True, gives a list of dictionaries, where each is the same as
            the result of ``info(path)``. If False, gives a list of paths
            (str).
        kwargs: may have additional backend-specific options, such as version
            information

        Returns
        -------
        List of strings if detail is False, or list of directory information
        dicts if detail is True.
        """
        raise NotImplementedError

    def _ls_from_cache(self, path):
        """Check cache for listing

        Returns listing, if found (may be empty list for a directly that exists
        but contains nothing), None if not in cache.
        """
        parent = self._parent(path)
        try:
            return self.dircache[path.rstrip("/")]
        except KeyError:
            pass
        try:
            files = [
                f
                for f in self.dircache[parent]
                if f["name"] == path
                or (f["name"] == path.rstrip("/") and f["type"] == "directory")
            ]
            if len(files) == 0:
                # parent dir was listed but did not contain this file
                raise FileNotFoundError(path)
            return files
        except KeyError:
            pass

    def walk(self, path, maxdepth=None, topdown=True, on_error="omit", **kwargs):
        """Return all files under the given path.

        List all files, recursing into subdirectories; output is iterator-style,
        like ``os.walk()``. For a simple list of files, ``find()`` is available.

        When topdown is True, the caller can modify the dirnames list in-place (perhaps
        using del or slice assignment), and walk() will
        only recurse into the subdirectories whose names remain in dirnames;
        this can be used to prune the search, impose a specific order of visiting,
        or even to inform walk() about directories the caller creates or renames before
        it resumes walk() again.
        Modifying dirnames when topdown is False has no effect. (see os.walk)

        Note that the "files" outputted will include anything that is not
        a directory, such as links.

        Parameters
        ----------
        path: str
            Root to recurse into
        maxdepth: int
            Maximum recursion depth. None means limitless, but not recommended
            on link-based file-systems.
        topdown: bool (True)
            Whether to walk the directory tree from the top downwards or from
            the bottom upwards.
        on_error: "omit", "raise", a callable
