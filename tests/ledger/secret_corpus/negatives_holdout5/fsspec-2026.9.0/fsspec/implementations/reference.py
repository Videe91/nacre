import base64
import collections
import io
import itertools
import logging
import math
import os
from functools import lru_cache
from itertools import chain
from typing import TYPE_CHECKING, Literal

import fsspec.core
from fsspec.spec import AbstractBufferedFile

try:
    import ujson as json
except ImportError:
    if not TYPE_CHECKING:
        import json

from fsspec.asyn import AsyncFileSystem
from fsspec.callbacks import DEFAULT_CALLBACK
from fsspec.core import filesystem, open, split_protocol
from fsspec.implementations.asyn_wrapper import AsyncFileSystemWrapper
from fsspec.utils import (
    check_contained,
    isfilelike,
    merge_offset_ranges,
    other_paths,
)

logger = logging.getLogger("fsspec.reference")


class ReferenceNotReachable(RuntimeError):
    def __init__(self, reference, target, *args):
        super().__init__(*args)
        self.reference = reference
        self.target = target

    def __str__(self):
        return f'Reference "{self.reference}" failed to fetch target {self.target}'


def _first(d):
    return next(iter(d.values()))


def _prot_in_references(path, references):
    ref = references.get(path)
    if isinstance(ref, (list, tuple)) and isinstance(ref[0], str):
        return split_protocol(ref[0])[0] if ref[0] else ref[0]


def _protocol_groups(paths, references):
    if isinstance(paths, str):
        return {_prot_in_references(paths, references): [paths]}
    out = {}
    for path in paths:
        protocol = _prot_in_references(path, references)
        out.setdefault(protocol, []).append(path)
    return out


class RefsValuesView(collections.abc.ValuesView):
    def __iter__(self):
        for val in self._mapping.zmetadata.values():
            yield json.dumps(val).encode()
        yield from self._mapping._items.values()
        for field in self._mapping.listdir():
            chunk_sizes = self._mapping._get_chunk_sizes(field)
            if len(chunk_sizes) == 0:
                yield self._mapping[field + "/0"]
                continue
            yield from self._mapping._generate_all_records(field)


class RefsItemsView(collections.abc.ItemsView):
    def __iter__(self):
        return zip(self._mapping.keys(), self._mapping.values())


def ravel_multi_index(idx, sizes):
    val = 0
    mult = 1
    for i, s in zip(idx[::-1], sizes[::-1]):
        val += i * mult
        mult *= s
    return val


class LazyReferenceMapper(collections.abc.MutableMapping):
    """This interface can be used to read/write references from Parquet stores.
    It is not intended for other types of references.
    It can be used with Kerchunk's MultiZarrToZarr method to combine
    references into a parquet store.
    Examples of this use-case can be found here:
    https://fsspec.github.io/kerchunk/advanced.html?highlight=parquet#parquet-storage"""

    # import is class level to prevent numpy dep requirement for fsspec
    @property
    def np(self):
        import numpy as np

        return np

    @property
    def pd(self):
        import pandas as pd

        return pd

    def __init__(
        self,
        root,
        fs=None,
        out_root=None,
        cache_size=128,
        categorical_threshold=10,
        engine: Literal["fastparquet", "pyarrow"] = "fastparquet",
    ):
        """

        This instance will be writable, storing changes in memory until full partitions
        are accumulated or .flush() is called.

        To create an empty lazy store, use .create()

        Parameters
        ----------
        root : str
            Root of parquet store
        fs : fsspec.AbstractFileSystem
            fsspec filesystem object, default is local filesystem.
        cache_size : int, default=128
            Maximum size of LRU cache, where cache_size*record_size denotes
            the total number of references that can be loaded in memory at once.
        categorical_threshold : int
            Encode urls as pandas.Categorical to reduce memory footprint if the ratio
            of the number of unique urls to total number of refs for each variable
            is greater than or equal to this number. (default 10)
        engine: Literal["fastparquet","pyarrow"]
            Engine choice for reading parquet files. (default is "fastparquet")
        """

        self.root = root
        self.chunk_sizes = {}
        self.cat_thresh = categorical_threshold
        self.engine = engine
        self.cache_size = cache_size
        self.url = self.root + "/{field}/refs.{record}.parq"
        # TODO: derive fs from `root`
        self.fs = fsspec.filesystem("file") if fs is None else fs
        self.out_root = self.fs.unstrip_protocol(out_root or self.root)

        from importlib.util import find_spec

        if self.engine == "pyarrow" and find_spec("pyarrow") is None:
            raise ImportError("engine choice `pyarrow` is not installed.")

        # Apply `lru_cache` decorator manually per instance.
        # This way `self` reference is not held on class level.
        # WARNING: However, this means that self and its members are not reflected
        # in the cache key, so we expect they won't be mutated once a value is cached.
        self.listdir = lru_cache()(self.listdir)
        self._key_to_record = lru_cache(maxsize=4096)(self._key_to_record)

    def __getattr__(self, item):
        if item in ("_items", "record_size", "zmetadata"):
            self.setup()
            # avoid possible recursion if setup fails somehow
            return self.__dict__[item]
        raise AttributeError(item)

    def setup(self):
        self._items = {}
        self._items[".zmetadata"] = self.fs.cat_file(
            "/".join([self.root, ".zmetadata"])
        )
        met = json.loads(self._items[".zmetadata"])
        self.record_size = met["record_size"]
        self.zmetadata = met["metadata"]

        # Define function to open and decompress refs
        @lru_cache(maxsize=self.cache_size)
        def open_refs(field, record):
            """cached parquet file loader"""
            path = self.url.format(field=field, record=record)
            data = io.BytesIO(self.fs.cat_file(path))
            try:
                df = self.pd.read_parquet(data, engine=self.engine)
                refs = {c: df[c].to_numpy(copy=True) for c in df.columns}
            except OSError:
                refs = None
            return refs

        self.open_refs = open_refs

    @staticmethod
    def create(root, storage_options=None, fs=None, record_size=10000, **kwargs):
        """Make empty parquet reference set

        First deletes the contents of the given directory, if it exists.

        Parameters
        ----------
        root: str
            Directory to contain the output; will be created
        storage_options: dict | None
            For making the filesystem to use for writing is fs is None
        fs: FileSystem | None
            Filesystem for writing
        record_size: int
            Number of references per parquet file
        kwargs: passed to __init__

        Returns
        -------
        LazyReferenceMapper instance
        """
        met = {"metadata": {}, "record_size": record_size}
        if fs is None:
            fs, root = fsspec.core.url_to_fs(root, **(storage_options or {}))
        if fs.exists(root):
            fs.rm(root, recursive=True)
        fs.makedirs(root, exist_ok=True)
        fs.pipe("/".join([root, ".zmetadata"]), json.dumps(met).encode())
        return LazyReferenceMapper(root, fs, **kwargs)

    def listdir(self):
        """List top-level directories"""
        dirs = (p.rsplit("/", 1)[0] for p in self.zmetadata if not p.startswith(".z"))
        return set(dirs)

    def ls(self, path="", detail=True):
        """Shortcut file listings"""
        path = path.rstrip("/")
        pathdash = path + "/" if path else ""
        dirnames = self.listdir()
        dirs = [
            d
            for d in dirnames
            if d.startswith(pathdash) and "/" not in d.lstrip(pathdash)
        ]
        if dirs:
            others = {
                f
                for f in chain(
                    [".zmetadata"],
                    (name for name in self.zmetadata),
                    (name for name in self._items),
                )
                if f.startswith(pathdash) and "/" not in f.lstrip(pathdash)
            }
            if detail is False:
                others.update(dirs)
                return sorted(others)
            dirinfo = [{"name": name, "type": "directory", "size": 0} for name in dirs]
            fileinfo = [
                {
                    "name": name,
                    "type": "file",
                    "size": len(
                        json.dumps(self.zmetadata[name])
                        if name in self.zmetadata
                        else self._items[name]
                    ),
                }
                for name in others
            ]
            return sorted(dirinfo + fileinfo, key=lambda s: s["name"])
        field = path
        others = set(
            [name for name in self.zmetadata if name.startswith(f"{path}/")]
            + [name for name in self._items if name.startswith(f"{path}/")]
        )
        fileinfo = [
            {
                "name": name,
                "type": "file",
                "size": len(
                    json.dumps(self.zmetadata[name])
                    if name in self.zmetadata
                    else self._items[name]
                ),
            }
            for name in others
        ]
        keys = self._keys_in_field(field)

        if detail is False:
            return list(others) + list(keys)
        recs = self._generate_all_records(field)
        recinfo = [
            {"name": name, "type": "file", "size": rec[-1]}
            for name, rec in zip(keys, recs)
            if rec[0]  # filters out path==None, deleted/missing
        ]
        return fileinfo + recinfo

    def _load_one_key(self, key):
        """Get the reference for one key

        Returns bytes, one-element list or three-element list.
        """
        if key in self._items:
            return self._items[key]
        elif key in self.zmetadata:
            return json.dumps(self.zmetadata[key]).encode()
        elif "/" not in key or self._is_meta(key):
            raise KeyError(key)
        field, _ = key.rsplit("/", 1)
        record, ri, chunk_size = self._key_to_record(key)
        maybe = self._items.get((field, record), {}).get(ri, False)
        if maybe is None:
            # explicitly deleted
            raise KeyError
        elif maybe:
            return maybe
        elif chunk_size == 0:
            return b""

        # Chunk keys can be loaded from row group and cached in LRU cache
        try:
            refs = self.open_refs(field, record)
        except (ValueError, TypeError, FileNotFoundError) as exc:
            raise KeyError(key) from exc
        columns = ["path", "offset", "size", "raw"]
        selection = [refs[c][ri] if c in refs else None for c in columns]
        raw = selection[-1]
        if raw is not None:
            return raw
        if selection[0] is None:
            raise KeyError("This reference does not exist or has been deleted")
        if selection[1:3] == [0, 0]:
            # URL only
            return selection[:1]
        # URL, offset, size
        return selection[:3]

    def _key_to_record(self, key):
        """Details needed to construct a reference for one key"""
        field, chunk = key.rsplit("/", 1)
        chunk_sizes = self._get_chunk_sizes(field)
        if len(chunk_sizes) == 0:
            return 0, 0, 0
        chunk_idx = [int(c) for c in chunk.split(".")]
        chunk_number = ravel_multi_index(chunk_idx, chunk_sizes)
        record = chunk_number // self.record_size
        ri = chunk_number % self.record_size
        return record, ri, len(chunk_sizes)

    def _get_chunk_sizes(self, field):
        """The number of chunks along each axis for a given field"""
        if field not in self.chunk_sizes:
            zarray = self.zmetadata[f"{field}/.zarray"]
            size_ratio = [
                math.ceil(s / c) for s, c in zip(zarray["shape"], zarray["chunks"])
            ]
            self.chunk_sizes[field] = size_ratio or [1]
        return self.chunk_sizes[field]

    def _generate_record(self, field, record):
        """The references for a given parquet file of a given field"""
        refs = self.open_refs(field, record)
        it = iter(zip(*refs.values()))
        if len(refs) == 3:
            # All urls
            return (list(t) for t in it)
        elif len(refs) == 1:
            # All raws
            return refs["raw"]
        else:
            # Mix of urls and raws
            return (list(t[:3]) if not t[3] else t[3] for t in it)

    def _generate_all_records(self, field):
        """Load all the references within a field by iterating over the parquet files"""
        nrec = 1
        for ch in self._get_chunk_sizes(field):
            nrec *= ch
        nrec = math.ceil(nrec / self.record_size)
        for record in range(nrec):
            yield from self._generate_record(field, record)

    def values(self):
        return RefsValuesView(self)

    def items(self):
        return RefsItemsView(self)

    def __hash__(self):
        return id(self)

    def __getitem__(self, key):
        return self._load_one_key(key)

    def __setitem__(self, key, value):
        if "/" in key and not self._is_meta(key):
            field, chunk = key.rsplit("/", 1)
            record, i, _ = self._key_to_record(key)
            subdict = self._items.setdefault((field, record), {})
            subdict[i] = value
            if len(subdict) == self.record_size:
                self.write(field, record)
        else:
            # metadata or top-level
            if hasattr(value, "to_bytes"):
                val = value.to_bytes().decode()
            elif isinstance(value, bytes):
                val = value.decode()
            else:
                val = value
            self._items[key] = val
            new_value = json.loads(val)
            self.zmetadata[key] = {**self.zmetadata.get(key, {}), **new_value}

    @staticmethod
    def _is_meta(key):
        return key.startswith(".z") or "/.z" in key

    def __delitem__(self, key):
        if key in self._items:
            del self._items[key]
        elif key in self.zmetadata:
            del self.zmetadata[key]
        else:
            if "/" in key and not self._is_meta(key):
                field, _ = key.rsplit("/", 1)
                record, i, _ = self._key_to_record(key)
                subdict = self._items.setdefault((field, record), {})
                subdict[i] = None
                if len(subdict) == self.record_size:
                    self.write(field, record)
            else:
                # metadata or top-level
                self._items[key] = None

    def write(self, field, record, base_url=None, storage_options=None):
        # extra requirements if writing
        import kerchunk.df
        import numpy as np
        import pandas as pd

        partition = self._items[(field, record)]
        original = False
        if len(partition) < self.record_size:
            try:
                original = self.open_refs(field, record)
            except OSError:
                pass

        if original:
            paths = original["path"]
            offsets = original["offset"]
            sizes = original["size"]
            raws = original["raw"]
        else:
            paths = np.full(self.record_size, np.nan, dtype="O")
            offsets = np.zeros(self.record_size, dtype="int64")
            sizes = np.zeros(self.record_size, dtype="int64")
            raws = np.full(self.record_size, np.nan, dtype="O")
        for j, data in partition.items():
            if isinstance(data, list):
                if (
                    str(paths.dtype) == "category"
                    and data[0] not in paths.dtype.categories
                ):
                    paths = paths.add_categories(data[0])
                paths[j] = data[0]
                if len(data) > 1:
                    offsets[j] = data[1]
                    sizes[j] = data[2]
            elif data is None:
