from __future__ import annotations

import os
import pathlib
import sys
from collections.abc import (
    AsyncIterator,
    Callable,
    Iterable,
    Iterator,
    Sequence,
)
from dataclasses import dataclass
from functools import partial
from os import PathLike
from typing import (
    IO,
    TYPE_CHECKING,
    Any,
    AnyStr,
    ClassVar,
    Final,
    Generic,
    TypeVar,
    overload,
)

from .. import to_thread
from ..abc import AsyncResource
from ._synchronization import CapacityLimiter

if sys.version_info >= (3, 11):
    from typing import Self
else:
    from typing_extensions import Self

if sys.version_info >= (3, 14):
    from pathlib.types import PathInfo

if TYPE_CHECKING:
    from types import ModuleType

    from _typeshed import OpenBinaryMode, OpenTextMode, ReadableBuffer, WriteableBuffer
else:
    ReadableBuffer = OpenBinaryMode = OpenTextMode = WriteableBuffer = object


T = TypeVar("T", bound="Path")


class AsyncFile(AsyncResource, Generic[AnyStr]):
    """
    An asynchronous file object.

    This class wraps a standard file object and provides async friendly versions of the
    following blocking methods (where available on the original file object):

    * read
    * read1
    * readline
    * readlines
    * readinto
    * readinto1
    * write
    * writelines
    * truncate
    * seek
    * tell
    * flush

    All other methods are directly passed through.

    This class supports the asynchronous context manager protocol which closes the
    underlying file at the end of the context block.

    This class also supports asynchronous iteration::

        async with await open_file(...) as f:
            async for line in f:
                print(line)
    """

    def __init__(
        self, fp: IO[AnyStr], *, limiter: CapacityLimiter | None = None
    ) -> None:
        if limiter is not None and not isinstance(limiter, CapacityLimiter):
            raise TypeError(
                f"limiter must be a CapacityLimiter or None, not "
                f"{limiter.__class__.__name__}"
            )

        self._fp: Any = fp
        self._limiter = limiter

    def __getattr__(self, name: str) -> object:
        return getattr(self._fp, name)

    @property
    def limiter(self) -> CapacityLimiter | None:
        """The capacity limiter used by this file object, if not the global limiter."""
        return self._limiter

    @property
    def wrapped(self) -> IO[AnyStr]:
        """The wrapped file object."""
        return self._fp

    async def __aiter__(self) -> AsyncIterator[AnyStr]:
        while True:
            line = await self.readline()
            if line:
                yield line
            else:
                break

    async def aclose(self) -> None:
        return await to_thread.run_sync(self._fp.close, limiter=self._limiter)

    async def read(self, size: int = -1) -> AnyStr:
        return await to_thread.run_sync(self._fp.read, size, limiter=self._limiter)

    async def read1(self: AsyncFile[bytes], size: int = -1) -> bytes:
        return await to_thread.run_sync(self._fp.read1, size, limiter=self._limiter)

    async def readline(self) -> AnyStr:
        return await to_thread.run_sync(self._fp.readline, limiter=self._limiter)

    async def readlines(self) -> list[AnyStr]:
        return await to_thread.run_sync(self._fp.readlines, limiter=self._limiter)

    async def readinto(self: AsyncFile[bytes], b: WriteableBuffer) -> int:
        return await to_thread.run_sync(self._fp.readinto, b, limiter=self._limiter)

    async def readinto1(self: AsyncFile[bytes], b: WriteableBuffer) -> int:
        return await to_thread.run_sync(self._fp.readinto1, b, limiter=self._limiter)

    @overload
    async def write(self: AsyncFile[bytes], b: ReadableBuffer) -> int: ...

    @overload
    async def write(self: AsyncFile[str], b: str) -> int: ...

    async def write(self, b: ReadableBuffer | str) -> int:
        return await to_thread.run_sync(self._fp.write, b, limiter=self._limiter)

    @overload
    async def writelines(
        self: AsyncFile[bytes], lines: Iterable[ReadableBuffer]
    ) -> None: ...

    @overload
    async def writelines(self: AsyncFile[str], lines: Iterable[str]) -> None: ...

    async def writelines(self, lines: Iterable[ReadableBuffer] | Iterable[str]) -> None:
        return await to_thread.run_sync(
            self._fp.writelines, lines, limiter=self._limiter
        )

    async def truncate(self, size: int | None = None) -> int:
        return await to_thread.run_sync(self._fp.truncate, size, limiter=self._limiter)

    async def seek(self, offset: int, whence: int | None = os.SEEK_SET) -> int:
        return await to_thread.run_sync(
            self._fp.seek, offset, whence, limiter=self._limiter
        )

    async def tell(self) -> int:
        return await to_thread.run_sync(self._fp.tell, limiter=self._limiter)

    async def flush(self) -> None:
        return await to_thread.run_sync(self._fp.flush, limiter=self._limiter)


@overload
async def open_file(
    file: str | PathLike[str] | int,
    mode: OpenBinaryMode,
    buffering: int = ...,
    encoding: str | None = ...,
    errors: str | None = ...,
    newline: str | None = ...,
    closefd: bool = ...,
    opener: Callable[[str, int], int] | None = ...,
    *,
    limiter: CapacityLimiter | None = ...,
) -> AsyncFile[bytes]: ...


@overload
async def open_file(
    file: str | PathLike[str] | int,
    mode: OpenTextMode = ...,
    buffering: int = ...,
    encoding: str | None = ...,
    errors: str | None = ...,
    newline: str | None = ...,
    closefd: bool = ...,
    opener: Callable[[str, int], int] | None = ...,
    *,
    limiter: CapacityLimiter | None = ...,
) -> AsyncFile[str]: ...


async def open_file(
    file: str | PathLike[str] | int,
    mode: str = "r",
    buffering: int = -1,
    encoding: str | None = None,
    errors: str | None = None,
    newline: str | None = None,
    closefd: bool = True,
    opener: Callable[[str, int], int] | None = None,
    *,
    limiter: CapacityLimiter | None = None,
) -> AsyncFile[Any]:
    """
    Open a file asynchronously.

    Except for ``limiter``, the arguments are exactly the same as for the builtin :func:`open`.

    :param limiter: an optional capacity limiter to use with the file
        instead of the default one
    :return: an asynchronous file object

    .. versionchanged:: 4.14.0
        Added the ``limiter`` keyword argument.

    """
    fp = await to_thread.run_sync(
        open,
        file,
        mode,
        buffering,
        encoding,
        errors,
        newline,
        closefd,
        opener,
        limiter=limiter,
    )
    return AsyncFile(fp, limiter=limiter)


def wrap_file(
    file: IO[AnyStr], *, limiter: CapacityLimiter | None = None
) -> AsyncFile[AnyStr]:
    """
    Wrap an existing file as an asynchronous file.

    :param file: an existing file-like object
    :param limiter: an optional capacity limiter to use with the file
        instead of the default one
    :return: an asynchronous file object

    .. versionchanged:: 4.14.0
        Added the ``limiter`` keyword argument.

    """
    return AsyncFile(file, limiter=limiter)


@dataclass(eq=False)
class _PathIterator(AsyncIterator[T]):
    iterator: Iterator[PathLike[str]]
    limiter: CapacityLimiter | None
    # This was added to ensure that iterating over a subclass of Path yields instances
    # of that subclass rather than the base Path class.
    path_cls: type[T]

    async def __anext__(self) -> T:
        nextval = await to_thread.run_sync(
            next, self.iterator, None, abandon_on_cancel=True, limiter=self.limiter
        )
        if nextval is None:
            raise StopAsyncIteration from None

        return self.path_cls(nextval, limiter=self.limiter)


class Path:
    """
    An asynchronous version of :class:`pathlib.Path`.

    This class cannot be substituted for :class:`pathlib.Path` or
    :class:`pathlib.PurePath`, but it is compatible with the :class:`os.PathLike`
    interface.

    It implements the Python 3.10 version of :class:`pathlib.Path` interface, except for
    the deprecated :meth:`~pathlib.Path.link_to` method.

    Some methods may be unavailable or have limited functionality, based on the Python
    version:

    * :meth:`~pathlib.Path.copy` (available on Python 3.14 or later)
    * :meth:`~pathlib.Path.copy_into` (available on Python 3.14 or later)
    * :meth:`~pathlib.Path.from_uri` (available on Python 3.13 or later)
    * :meth:`~pathlib.PurePath.full_match` (available on Python 3.13 or later)
    * :attr:`~pathlib.Path.info` (available on Python 3.14 or later)
    * :meth:`~pathlib.Path.is_junction` (available on Python 3.12 or later)
    * :meth:`~pathlib.PurePath.match` (the ``case_sensitive`` parameter is only
      available on Python 3.13 or later)
    * :meth:`~pathlib.Path.move` (available on Python 3.14 or later)
    * :meth:`~pathlib.Path.move_into` (available on Python 3.14 or later)
    * :meth:`~pathlib.PurePath.relative_to` (the ``walk_up`` parameter is only available
      on Python 3.12 or later)
    * :meth:`~pathlib.Path.walk` (available on Python 3.12 or later)

    Any methods that do disk I/O need to be awaited on. These methods are:

    * :meth:`~pathlib.Path.absolute`
    * :meth:`~pathlib.Path.chmod`
    * :meth:`~pathlib.Path.cwd`
    * :meth:`~pathlib.Path.exists`
    * :meth:`~pathlib.Path.expanduser`
    * :meth:`~pathlib.Path.group`
    * :meth:`~pathlib.Path.hardlink_to`
    * :meth:`~pathlib.Path.home`
    * :meth:`~pathlib.Path.is_block_device`
    * :meth:`~pathlib.Path.is_char_device`
    * :meth:`~pathlib.Path.is_dir`
    * :meth:`~pathlib.Path.is_fifo`
    * :meth:`~pathlib.Path.is_file`
    * :meth:`~pathlib.Path.is_junction`
    * :meth:`~pathlib.Path.is_mount`
    * :meth:`~pathlib.Path.is_socket`
    * :meth:`~pathlib.Path.is_symlink`
    * :meth:`~pathlib.Path.lchmod`
    * :meth:`~pathlib.Path.lstat`
    * :meth:`~pathlib.Path.mkdir`
    * :meth:`~pathlib.Path.open`
    * :meth:`~pathlib.Path.owner`
    * :meth:`~pathlib.Path.read_bytes`
    * :meth:`~pathlib.Path.read_text`
    * :meth:`~pathlib.Path.readlink`
    * :meth:`~pathlib.Path.rename`
    * :meth:`~pathlib.Path.replace`
    * :meth:`~pathlib.Path.resolve`
    * :meth:`~pathlib.Path.rmdir`
    * :meth:`~pathlib.Path.samefile`
    * :meth:`~pathlib.Path.stat`
    * :meth:`~pathlib.Path.symlink_to`
    * :meth:`~pathlib.Path.touch`
    * :meth:`~pathlib.Path.unlink`
    * :meth:`~pathlib.Path.walk`
    * :meth:`~pathlib.Path.write_bytes`
    * :meth:`~pathlib.Path.write_text`

    Additionally, the following methods return an async iterator yielding
    :class:`~.Path` objects:

    * :meth:`~pathlib.Path.glob`
    * :meth:`~pathlib.Path.iterdir`
    * :meth:`~pathlib.Path.rglob`

    .. versionchanged:: 4.14.0
        Added the ``limiter`` keyword argument.
    """

    __slots__ = "__weakref__", "_limiter", "_path"

    __weakref__: Any

    def __init__(
        self, *args: str | PathLike[str], limiter: CapacityLimiter | None = None
    ) -> None:
        if limiter is not None and not isinstance(limiter, CapacityLimiter):
            raise TypeError(
                f"limiter must be a CapacityLimiter or None, not "
                f"{limiter.__class__.__name__}"
            )

        self._path: Final[pathlib.Path] = pathlib.Path(*args)
        self._limiter = limiter

    def __fspath__(self) -> str:
        return self._path.__fspath__()

    if sys.version_info >= (3, 15):

        def __vfspath__(self) -> str:
            return self._path.__vfspath__()

    def __str__(self) -> str:
        return self._path.__str__()

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.as_posix()!r})"

    def __bytes__(self) -> bytes:
        return self._path.__bytes__()

    def __hash__(self) -> int:
        return self._path.__hash__()

    def __eq__(self, other: object) -> bool:
        target = other._path if isinstance(other, Path) else other
        return self._path.__eq__(target)

    def __lt__(self, other: pathlib.PurePath | Path) -> bool:
        target = other._path if isinstance(other, Path) else other
        return self._path.__lt__(target)

    def __le__(self, other: pathlib.PurePath | Path) -> bool:
        target = other._path if isinstance(other, Path) else other
        return self._path.__le__(target)

    def __gt__(self, other: pathlib.PurePath | Path) -> bool:
        target = other._path if isinstance(other, Path) else other
        return self._path.__gt__(target)

    def __ge__(self, other: pathlib.PurePath | Path) -> bool:
        target = other._path if isinstance(other, Path) else other
        return self._path.__ge__(target)

    def __truediv__(self, other: str | PathLike[str]) -> Self:
        return type(self)(self._path / other, limiter=self._limiter)

    def __rtruediv__(self, other: str | PathLike[str]) -> Self:
        return type(self)(other, limiter=self._limiter) / self

    @property
    def limiter(self) -> CapacityLimiter | None:
        """The capacity limiter used by this path, if not the global limiter."""
        return self._limiter

    @property
    def parts(self) -> tuple[str, ...]:
        return self._path.parts

    @property
    def drive(self) -> str:
        return self._path.drive

    @property
    def root(self) -> str:
        return self._path.root

    @property
    def anchor(self) -> str:
        return self._path.anchor

    @property
    def parents(self) -> Sequence[Self]:
        return tuple(type(self)(p, limiter=self._limiter) for p in self._path.parents)

    @property
    def parent(self) -> Self:
        return type(self)(self._path.parent, limiter=self._limiter)

    @property
    def name(self) -> str:
        return self._path.name

    @property
    def suffix(self) -> str:
        return self._path.suffix

    @property
    def suffixes(self) -> list[str]:
        return self._path.suffixes

    @property
    def stem(self) -> str:
        return self._path.stem

    async def absolute(self) -> Self:
        path = await to_thread.run_sync(self._path.absolute, limiter=self._limiter)
        return type(self)(path, limiter=self._limiter)

    def as_posix(self) -> str:
        return self._path.as_posix()

    def as_uri(self) -> str:
        return self._path.as_uri()

    if sys.version_info >= (3, 13):
        parser: ClassVar[ModuleType] = pathlib.Path.parser

        @classmethod
        def from_uri(cls, uri: str, *, limiter: CapacityLimiter | None = None) -> Self:
            return cls(pathlib.Path.from_uri(uri), limiter=limiter)

        def full_match(
            self, path_pattern: str, *, case_sensitive: bool | None = None
        ) -> bool:
            return self._path.full_match(path_pattern, case_sensitive=case_sensitive)

        def match(
            self, path_pattern: str, *, case_sensitive: bool | None = None
        ) -> bool:
            return self._path.match(path_pattern, case_sensitive=case_sensitive)
    else:

        def match(self, path_pattern: str) -> bool:
            return self._path.match(path_pattern)

    if sys.version_info >= (3, 14):

        @property
        def info(self) -> PathInfo:
            return self._path.info

        async def copy(
            self,
            target: str | os.PathLike[str],
            *,
            follow_symlinks: bool = True,
            preserve_metadata: bool = False,
        ) -> Self:
            func = partial(
                self._path.copy,
                follow_symlinks=follow_symlinks,
                preserve_metadata=preserve_metadata,
            )
            return type(self)(
                await to_thread.run_sync(
                    func, pathlib.Path(target), limiter=self._limiter
                ),
                limiter=self._limiter,
            )

        async def copy_into(
            self,
            target_dir: str | os.PathLike[str],
            *,
            follow_symlinks: bool = True,
            preserve_metadata: bool = False,
        ) -> Self:
            func = partial(
                self._path.copy_into,
                follow_symlinks=follow_symlinks,
                preserve_metadata=preserve_metadata,
            )
            return type(self)(
