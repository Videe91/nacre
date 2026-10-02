from __future__ import annotations

import asyncio
import collections
import functools
import logging
import math
import os
import threading
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from itertools import groupby
from operator import itemgetter
from typing import TYPE_CHECKING, Any, ClassVar, Generic, NamedTuple, TypeVar

if TYPE_CHECKING:
    import mmap

    from typing_extensions import ParamSpec

    P = ParamSpec("P")
else:
    P = TypeVar("P")

T = TypeVar("T")


logger = logging.getLogger("fsspec.caching")

Fetcher = Callable[[int, int], bytes]  # Maps (start, end) to bytes
MultiFetcher = Callable[[list[int, int]], bytes]  # Maps [(start, end)] to bytes


class BaseCache:
    """Pass-though cache: doesn't keep anything, calls every time

    Acts as base class for other cachers

    Parameters
    ----------
    blocksize: int
        How far to read ahead in numbers of bytes
    fetcher: func
        Function of the form f(start, end) which gets bytes from remote as
        specified
    size: int
        How big this file is
    """

    name: ClassVar[str] = "none"

    def __init__(self, blocksize: int, fetcher: Fetcher, size: int) -> None:
        self.blocksize = blocksize
        self.nblocks = 0
        self.fetcher = fetcher
        self.size = size
        self.hit_count = 0
        self.miss_count = 0
        # the bytes that we actually requested
        self.total_requested_bytes = 0

    def _fetch(self, start: int | None, stop: int | None) -> bytes:
        if start is None:
            start = 0
        if stop is None:
            stop = self.size
        if start >= self.size or start >= stop:
            return b""
        return self.fetcher(start, stop)

    def _reset_stats(self) -> None:
        """Reset hit and miss counts for a more ganular report e.g. by file."""
        self.hit_count = 0
        self.miss_count = 0
        self.total_requested_bytes = 0

    def _log_stats(self) -> str:
        """Return a formatted string of the cache statistics."""
        if self.hit_count == 0 and self.miss_count == 0:
            # a cache that does nothing, this is for logs only
            return ""
        return f" , {self.name}: {self.hit_count} hits, {self.miss_count} misses, {self.total_requested_bytes} total requested bytes"

    def __repr__(self) -> str:
        # TODO: use rich for better formatting
        return f"""
        <{self.__class__.__name__}:
            block size  :   {self.blocksize}
            block count :   {self.nblocks}
            file size   :   {self.size}
            cache hits  :   {self.hit_count}
            cache misses:   {self.miss_count}
            total requested bytes: {self.total_requested_bytes}>
        """


class MMapCache(BaseCache):
    """memory-mapped sparse file cache

    Opens temporary file, which is filled blocks-wise when data is requested.
    Ensure there is enough disc space in the temporary location.

    This cache method might only work on posix

    Parameters
    ----------
    blocksize: int
        How far to read ahead in numbers of bytes
    fetcher: Fetcher
        Function of the form f(start, end) which gets bytes from remote as
        specified
    size: int
        How big this file is
    location: str
        Where to create the temporary file. If None, a temporary file is
        created using tempfile.TemporaryFile().
    blocks: set[int]
        Set of block numbers that have already been fetched. If None, an empty
        set is created.
    multi_fetcher: MultiFetcher
        Function of the form f([(start, end)]) which gets bytes from remote
        as specified. This function is used to fetch multiple blocks at once.
        If not specified, the fetcher function is used instead.
    """

    name = "mmap"

    def __init__(
        self,
        blocksize: int,
        fetcher: Fetcher,
        size: int,
        location: str | None = None,
        blocks: set[int] | None = None,
        multi_fetcher: MultiFetcher | None = None,
    ) -> None:
        super().__init__(blocksize, fetcher, size)
        self.blocks = set() if blocks is None else blocks
        self.location = location
        self.multi_fetcher = multi_fetcher
        self.cache = self._makefile()

    def _makefile(self) -> mmap.mmap | bytearray:
        import mmap
        import tempfile

        if self.size == 0:
            return bytearray()

        # posix version
        if self.location is None or not os.path.exists(self.location):
            if self.location is None:
                fd = tempfile.TemporaryFile()
                self.blocks = set()
            else:
                fd = open(self.location, "wb+")
            fd.seek(self.size - 1)
            fd.write(b"1")
            fd.flush()
        else:
            fd = open(self.location, "r+b")

        return mmap.mmap(fd.fileno(), self.size)

    def _fetch(self, start: int | None, end: int | None) -> bytes:
        logger.debug(f"MMap cache fetching {start}-{end}")
        if start is None:
            start = 0
        if end is None:
            end = self.size
        if start >= self.size or start >= end:
            return b""
        start_block = start // self.blocksize
        end_block = end // self.blocksize
        block_range = range(start_block, end_block + 1)
        # Determine which blocks need to be fetched. This sequence is sorted by construction.
        need = (i for i in block_range if i not in self.blocks)
        # Count the number of blocks already cached
        self.hit_count += sum(1 for i in block_range if i in self.blocks)

        ranges = []

        # Consolidate needed blocks.
        # Algorithm adapted from Python 2.x itertools documentation.
        # We are grouping an enumerated sequence of blocks. By comparing when the difference
        # between an ascending range (provided by enumerate) and the needed block numbers
        # we can detect when the block number skips values. The key computes this difference.
        # Whenever the difference changes, we know that we have previously cached block(s),
        # and a new group is started. In other words, this algorithm neatly groups
        # runs of consecutive block numbers so they can be fetched together.
        for _, _blocks in groupby(enumerate(need), key=lambda x: x[0] - x[1]):
            # Extract the blocks from the enumerated sequence
            _blocks = tuple(map(itemgetter(1), _blocks))
            # Compute start of first block
            sstart = _blocks[0] * self.blocksize
            # Compute the end of the last block. Last block may not be full size.
            send = min(_blocks[-1] * self.blocksize + self.blocksize, self.size)

            # Fetch bytes (could be multiple consecutive blocks)
            self.total_requested_bytes += send - sstart
            logger.debug(
                f"MMap get blocks {_blocks[0]}-{_blocks[-1]} ({sstart}-{send})"
            )
            ranges.append((sstart, send))

            # Update set of cached blocks
            self.blocks.update(_blocks)
            # Update cache statistics with number of blocks we had to cache
            self.miss_count += len(_blocks)

        if not ranges:
            return self.cache[start:end]

        if self.multi_fetcher:
            logger.debug(f"MMap get blocks {ranges}")
            for idx, r in enumerate(self.multi_fetcher(ranges)):
                sstart, send = ranges[idx]
                logger.debug(f"MMap copy block ({sstart}-{send}")
                self.cache[sstart:send] = r
        else:
            for sstart, send in ranges:
                logger.debug(f"MMap get block ({sstart}-{send}")
                self.cache[sstart:send] = self.fetcher(sstart, send)

        return self.cache[start:end]

    def __getstate__(self) -> dict[str, Any]:
        state = self.__dict__.copy()
        # Remove the unpicklable entries.
        del state["cache"]
        return state

    def __setstate__(self, state: dict[str, Any]) -> None:
        # Restore instance attributes
        self.__dict__.update(state)
        self.cache = self._makefile()


class ReadAheadCache(BaseCache):
    """Cache which reads only when we get beyond a block of data

    This is a much simpler version of BytesCache, and does not attempt to
    fill holes in the cache or keep fragments alive. It is best suited to
    many small reads in a sequential order (e.g., reading lines from a file).
    """

    name = "readahead"

    def __init__(self, blocksize: int, fetcher: Fetcher, size: int) -> None:
        super().__init__(blocksize, fetcher, size)
        self.cache = b""
        self.start = 0
        self.end = 0

    def _fetch(self, start: int | None, end: int | None) -> bytes:
        if start is None:
            start = 0
        if end is None or end > self.size:
            end = self.size
        if start >= self.size or start >= end:
            return b""
        l = end - start
        if start >= self.start and end <= self.end:
            # cache hit
            self.hit_count += 1
            return self.cache[start - self.start : end - self.start]
        elif self.start <= start < self.end:
            # partial hit
            self.miss_count += 1
            part = self.cache[start - self.start :]
            l -= len(part)
            start = self.end
        else:
            # miss
            self.miss_count += 1
            part = b""
        end = min(self.size, end + self.blocksize)
        self.total_requested_bytes += end - start
        self.cache = self.fetcher(start, end)  # new block replaces old
        self.start = start
        self.end = self.start + len(self.cache)
        return part + self.cache[:l]


class AdaptiveReadaheadCache(BaseCache):
    """Cache with adaptive asynchronous prefetching.

    Optimized for sequential and near-sequential reads by dynamically
    adjusting the amount of data prefetched in the background. The cache
    uses the generic prefetch engine when async loop support is available,
    and falls back to ``ReadAheadCache`` when it is not.

    Parameters
    ----------
    blocksize: int
        Nominal read size used by callers.
    fetcher: Fetcher
        Function of the form ``f(start, end)`` that returns bytes.
    size: int
        Total size of the file.
    concurrency: int
        Maximum number of concurrent background fetch tasks.
    max_prefetch_size: int | None
        Optional upper bound for adaptive prefetch size in bytes.
    """

    name = "adaptive"

    def __init__(
        self,
        blocksize: int,
        fetcher: Fetcher,
        size: int,
        concurrency: int = 4,
        max_prefetch_size: int | None = None,
    ) -> None:
        super().__init__(blocksize, fetcher, size)
        self._fallback = ReadAheadCache(blocksize, fetcher, size)
        self._prefetcher = None

        async def _default_fetcher_async(
            start_offset: int,
            total_size: int,
            split_factor: int = 1,
        ) -> bytes:
            del split_factor
            return await asyncio.to_thread(
                self.fetcher, start_offset, start_offset + total_size
            )

        try:
            from . import asyn as fsspec_asyn
        except ImportError as e:
            raise ImportError(
                "AdaptiveReadaheadCache requires fsspec.asyn to be available"
            ) from e

        try:
            from .prefetcher import BackgroundPrefetcher

            self._prefetcher = BackgroundPrefetcher(
                fetcher=_default_fetcher_async,
                size=size,
                concurrency=concurrency,
                max_prefetch_size=max_prefetch_size,
                loop=fsspec_asyn.get_loop(),
            )
            logger.info(
                "AdaptiveReadaheadCache enabled (blocksize=%d, size=%d, concurrency=%d, max_prefetch_size=%s)",
                blocksize,
                size,
                concurrency,
                max_prefetch_size,
            )
        except Exception as e:
            logger.info(
                "AdaptiveReadaheadCache fallback to ReadAheadCache: %s",
                e,
                exc_info=True,
            )
            self._prefetcher = None

    def _fetch(self, start: int | None, end: int | None) -> bytes:
        if self._prefetcher is None:
            out = self._fallback._fetch(start, end)
            self.hit_count = self._fallback.hit_count
            self.miss_count = self._fallback.miss_count
            self.total_requested_bytes = self._fallback.total_requested_bytes
            return out

        out = self._prefetcher.fetch(start, end)
        self.miss_count += 1
        self.total_requested_bytes += len(out)
        return out

    def close(self) -> None:
        if self._prefetcher is not None:
            self._prefetcher.close()
            self._prefetcher = None

    def __getstate__(self) -> dict[str, Any]:
        # The prefetcher owns asyncio primitives that are not picklable.
        self.close()
        state = self.__dict__.copy()
        state["_prefetcher"] = None
        return state

    def __setstate__(self, state: dict[str, Any]) -> None:
        self.__dict__.update(state)
        self._prefetcher = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            # Best-effort cleanup during GC.
            pass


class FirstChunkCache(BaseCache):
    """Caches the first block of a file only

    This may be useful for file types where the metadata is stored in the header,
    but is randomly accessed.
    """

    name = "first"

    def __init__(self, blocksize: int, fetcher: Fetcher, size: int) -> None:
        if blocksize > size:
            # this will buffer the whole thing
            blocksize = size
        super().__init__(blocksize, fetcher, size)
        self.cache: bytes | None = None

    def _fetch(self, start: int | None, end: int | None) -> bytes:
        start = start or 0
        if start > self.size:
            logger.debug("FirstChunkCache: requested start > file size")
            return b""

        if end is None:
            end = self.size
        end = min(end, self.size)

        if start < self.blocksize:
            if self.cache is None:
                self.miss_count += 1
                if end > self.blocksize:
                    self.total_requested_bytes += end
                    data = self.fetcher(0, end)
                    self.cache = data[: self.blocksize]
                    return data[start:]
                self.cache = self.fetcher(0, self.blocksize)
                self.total_requested_bytes += self.blocksize
            part = self.cache[start:end]
            if end > self.blocksize:
                self.total_requested_bytes += end - self.blocksize
                part += self.fetcher(self.blocksize, end)
            self.hit_count += 1
            return part
        else:
            self.miss_count += 1
            self.total_requested_bytes += end - start
            return self.fetcher(start, end)


class BlockCache(BaseCache):
    """
    Cache holding memory as a set of blocks.

    Requests are only ever made ``blocksize`` at a time, and are
    stored in an LRU cache. The least recently accessed block is
    discarded when more than ``maxblocks`` are stored.

    Parameters
    ----------
    blocksize : int
        The number of bytes to store in each block.
        Requests are only ever made for ``blocksize``, so this
        should balance the overhead of making a request against
        the granularity of the blocks.
    fetcher : Callable
    size : int
        The total size of the file being cached.
    maxblocks : int
        The maximum number of blocks to cache for. The maximum memory
        use for this cache is then ``blocksize * maxblocks``.
    """

    name = "blockcache"

    def __init__(
        self, blocksize: int, fetcher: Fetcher, size: int, maxblocks: int = 32
    ) -> None:
        super().__init__(blocksize, fetcher, size)
        self.nblocks = math.ceil(size / blocksize)
        self.maxblocks = maxblocks
        self._fetch_block_cached = functools.lru_cache(maxblocks)(self._fetch_block)

