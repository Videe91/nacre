# mypy: allow-untyped-defs
from __future__ import annotations

import ast
from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import Mapping
from collections.abc import Sequence
import dataclasses
import inspect
from io import StringIO
import os
from pathlib import Path
import re
import sys
from traceback import extract_tb
from traceback import format_exception
from traceback import format_exception_only
from traceback import FrameSummary
from types import CodeType
from types import FrameType
from types import TracebackType
from typing import Any
from typing import ClassVar
from typing import Final
from typing import final
from typing import Generic
from typing import Literal
from typing import overload
from typing import SupportsIndex
from typing import TypeAlias
from typing import TypeVar

import pluggy

import _pytest
from _pytest._code.source import findsource
from _pytest._code.source import getrawcode
from _pytest._code.source import getstatementrange_ast
from _pytest._code.source import Source
from _pytest._io import TerminalWriter
from _pytest._io.saferepr import safeformat
from _pytest._io.saferepr import saferepr
from _pytest.compat import get_real_func
from _pytest.deprecated import check_ispytest
from _pytest.pathlib import absolutepath
from _pytest.pathlib import bestrelpath


if sys.version_info < (3, 11):
    from exceptiongroup import BaseExceptionGroup

TracebackStyle = Literal["long", "short", "line", "no", "native", "value", "auto"]

EXCEPTION_OR_MORE = type[BaseException] | tuple[type[BaseException], ...]


class Code:
    """Wrapper around Python code objects."""

    __slots__ = ("raw",)

    def __init__(self, obj: CodeType) -> None:
        self.raw = obj

    @classmethod
    def from_function(cls, obj: object) -> Code:
        return cls(getrawcode(obj))

    def __eq__(self, other):
        return self.raw == other.raw

    # Ignore type because of https://github.com/python/mypy/issues/4266.
    __hash__ = None  # type: ignore

    @property
    def firstlineno(self) -> int:
        return self.raw.co_firstlineno - 1

    @property
    def name(self) -> str:
        return self.raw.co_name

    @property
    def path(self) -> Path | str:
        """Return a path object pointing to source code, or an ``str`` in
        case of ``OSError`` / non-existing file."""
        filename = inspect.getfile(self.raw)
        if not filename:
            return ""
        try:
            p = absolutepath(filename)
            # maybe don't try this checking
            if not p.exists():
                raise OSError("path check failed.")
            return p
        except OSError:
            # XXX maybe try harder like the weird logic
            # in the standard lib [linecache.updatecache] does?
            return filename

    @property
    def fullsource(self) -> Source | None:
        """Return a _pytest._code.Source object for the full source file of the code."""
        full, _ = findsource(self.raw)
        return full

    def source(self) -> Source:
        """Return a _pytest._code.Source object for the code object's source only."""
        # return source only for that part of code
        return Source(self.raw)

    def getargs(self, var: bool = False) -> tuple[str, ...]:
        """Return a tuple with the argument names for the code object.

        If 'var' is set True also return the names of the variable and
        keyword arguments when present.
        """
        # inspect.getargs merges positional and kwonly into a single list;
        # co_argcount is needed to exclude kwonly when var=False.
        args, varargs, varkw = inspect.getargs(self.raw)
        if not var:
            return tuple(args[: self.raw.co_argcount])
        result = list(args)
        if varargs is not None:
            result.append(varargs)
        if varkw is not None:
            result.append(varkw)
        return tuple(result)


class Frame:
    """Wrapper around a Python frame holding f_locals and f_globals
    in which expressions can be evaluated."""

    __slots__ = ("raw",)

    def __init__(self, frame: FrameType) -> None:
        self.raw = frame

    @property
    def lineno(self) -> int:
        return self.raw.f_lineno - 1

    @property
    def f_globals(self) -> dict[str, Any]:
        return self.raw.f_globals

    @property
    def f_locals(self) -> dict[str, Any]:
        return self.raw.f_locals

    @property
    def code(self) -> Code:
        return Code(self.raw.f_code)

    @property
    def statement(self) -> Source:
        """Statement this frame is at."""
        if self.code.fullsource is None:
            return Source("")
        return self.code.fullsource.getstatement(self.lineno)

    def eval(self, code, **vars):
        """Evaluate 'code' in the frame.

        'vars' are optional additional local variables.

        Returns the result of the evaluation.
        """
        f_locals = self.f_locals.copy()
        f_locals.update(vars)
        return eval(code, self.f_globals, f_locals)

    def repr(self, object: object) -> str:
        """Return a 'safe' (non-recursive, one-line) string repr for 'object'."""
        return saferepr(object)

    def getargs(self, var: bool = False):
        """Return a list of tuples (name, value) for all arguments.

        If 'var' is set True, also include the variable and keyword arguments
        when present.
        """
        retval = []
        for arg in self.code.getargs(var):
            try:
                retval.append((arg, self.f_locals[arg]))
            except KeyError:
                pass  # this can occur when using Psyco
        return retval


class TracebackEntry:
    """A single entry in a Traceback."""

    __slots__ = ("_rawentry", "_repr_style")

    def __init__(
        self,
        rawentry: TracebackType,
        repr_style: Literal["short", "long"] | None = None,
    ) -> None:
        self._rawentry: Final = rawentry
        self._repr_style: Final = repr_style

    def with_repr_style(
        self, repr_style: Literal["short", "long"] | None
    ) -> TracebackEntry:
        return TracebackEntry(self._rawentry, repr_style)

    @property
    def lineno(self) -> int:
        return self._rawentry.tb_lineno - 1

    def get_python_framesummary(self) -> FrameSummary:
        # Python's built-in traceback module implements all the nitty gritty
        # details to get column numbers of out frames.
        stack_summary = extract_tb(self._rawentry, limit=1)
        return stack_summary[0]

    # Column and end line numbers introduced in python 3.11
    if sys.version_info < (3, 11):

        @property
        def end_lineno_relative(self) -> int | None:
            return None

        @property
        def colno(self) -> int | None:
            return None

        @property
        def end_colno(self) -> int | None:
            return None
    else:

        @property
        def end_lineno_relative(self) -> int | None:
            frame_summary = self.get_python_framesummary()
            if frame_summary.end_lineno is None:  # pragma: no cover
                return None
            return frame_summary.end_lineno - 1 - self.frame.code.firstlineno

        @property
        def colno(self) -> int | None:
            """Starting byte offset of the expression in the traceback entry."""
            return self.get_python_framesummary().colno

        @property
        def end_colno(self) -> int | None:
            """Ending byte offset of the expression in the traceback entry."""
            return self.get_python_framesummary().end_colno

    @property
    def frame(self) -> Frame:
        return Frame(self._rawentry.tb_frame)

    @property
    def relline(self) -> int:
        return self.lineno - self.frame.code.firstlineno

    def __repr__(self) -> str:
        return f"<TracebackEntry {self.frame.code.path}:{self.lineno + 1}>"

    @property
    def statement(self) -> Source:
        """_pytest._code.Source object for the current statement."""
        source = self.frame.code.fullsource
        assert source is not None
        return source.getstatement(self.lineno)

    @property
    def path(self) -> Path | str:
        """Path to the source code."""
        return self.frame.code.path

    @property
    def locals(self) -> dict[str, Any]:
        """Locals of underlying frame."""
        return self.frame.f_locals

    def getfirstlinesource(self) -> int:
        return self.frame.code.firstlineno

    def getsource(
        self, astcache: dict[str | Path, ast.AST] | None = None
    ) -> Source | None:
        """Return failing source code."""
        # we use the passed in astcache to not reparse asttrees
        # within exception info printing
        source = self.frame.code.fullsource
        if source is None:
            return None
        key = astnode = None
        if astcache is not None:
            key = self.frame.code.path
            if key is not None:
                astnode = astcache.get(key, None)
        start = self.getfirstlinesource()
        try:
            astnode, _, end = getstatementrange_ast(
                self.lineno, source, astnode=astnode
            )
        except SyntaxError:
            end = self.lineno + 1
        else:
            if key is not None and astcache is not None:
                astcache[key] = astnode
        return source[start:end]

    source = property(getsource)

    def ishidden(self, excinfo: ExceptionInfo[BaseException] | None) -> bool:
        """Return True if the current frame has a var __tracebackhide__
        resolving to True.

        If __tracebackhide__ is a callable, it gets called with the
        ExceptionInfo instance and can decide whether to hide the traceback.

        Mostly for internal use.
        """
        tbh: bool | Callable[[ExceptionInfo[BaseException] | None], bool] = False
        for maybe_ns_dct in (self.frame.f_locals, self.frame.f_globals):
            # in normal cases, f_locals and f_globals are dictionaries
            # however via `exec(...)` / `eval(...)` they can be other types
            # (even incorrect types!).
            # as such, we suppress all exceptions while accessing __tracebackhide__
            try:
                tbh = maybe_ns_dct["__tracebackhide__"]
            except Exception:
                pass
            else:
                break
        if tbh and callable(tbh):
            return tbh(excinfo)
        return tbh

    def __str__(self) -> str:
        name = self.frame.code.name
        try:
            line = str(self.statement).lstrip()
        except KeyboardInterrupt:
            raise
        except BaseException:
            line = "???"
        # This output does not quite match Python's repr for traceback entries,
        # but changing it to do so would break certain plugins.  See
        # https://github.com/pytest-dev/pytest/pull/7535/ for details.
        return f"  File '{self.path}':{self.lineno + 1} in {name}\n  {line}\n"

    @property
    def name(self) -> str:
        """co_name of underlying code."""
        return self.frame.code.raw.co_name


class Traceback(list[TracebackEntry]):
    """Traceback objects encapsulate and offer higher level access to Traceback entries."""

    def __init__(
        self,
        tb: TracebackType | Iterable[TracebackEntry],
    ) -> None:
        """Initialize from given python traceback object and ExceptionInfo."""
        if isinstance(tb, TracebackType):

            def f(cur: TracebackType) -> Iterable[TracebackEntry]:
                cur_: TracebackType | None = cur
                while cur_ is not None:
                    yield TracebackEntry(cur_)
                    cur_ = cur_.tb_next

            super().__init__(f(tb))
        else:
            super().__init__(tb)

    def cut(
        self,
        path: os.PathLike[str] | str | None = None,
        lineno: int | None = None,
        firstlineno: int | None = None,
        excludepath: os.PathLike[str] | None = None,
    ) -> Traceback:
        """Return a Traceback instance wrapping part of this Traceback.

        By providing any combination of path, lineno and firstlineno, the
        first frame to start the to-be-returned traceback is determined.

        This allows cutting the first part of a Traceback instance e.g.
        for formatting reasons (removing some uninteresting bits that deal
        with handling of the exception/traceback).
        """
        path_ = None if path is None else os.fspath(path)
        excludepath_ = None if excludepath is None else os.fspath(excludepath)
        for x in self:
            code = x.frame.code
            codepath = code.path
            if path is not None and str(codepath) != path_:
                continue
            if (
                excludepath is not None
                and isinstance(codepath, Path)
                and excludepath_ in (str(p) for p in codepath.parents)  # type: ignore[operator]
            ):
                continue
            if lineno is not None and x.lineno != lineno:
                continue
            if firstlineno is not None and x.frame.code.firstlineno != firstlineno:
                continue
            return Traceback(x._rawentry)
        return self

    @overload
    def __getitem__(self, key: SupportsIndex) -> TracebackEntry: ...

    @overload
    def __getitem__(self, key: slice) -> Traceback: ...

    def __getitem__(self, key: SupportsIndex | slice) -> TracebackEntry | Traceback:
        if isinstance(key, slice):
            return self.__class__(super().__getitem__(key))
        else:
            return super().__getitem__(key)

    def filter(
        self,
        excinfo_or_fn: ExceptionInfo[BaseException] | Callable[[TracebackEntry], bool],
        /,
    ) -> Traceback:
        """Return a Traceback instance with certain items removed.

        If the filter is an `ExceptionInfo`, removes all the ``TracebackEntry``s
        which are hidden (see ishidden() above).

        Otherwise, the filter is a function that gets a single argument, a
        ``TracebackEntry`` instance, and should return True when the item should
        be added to the ``Traceback``, False when not.
        """
        if isinstance(excinfo_or_fn, ExceptionInfo):
            fn = lambda x: not x.ishidden(excinfo_or_fn)  # noqa: E731
        else:
            fn = excinfo_or_fn
        return Traceback(filter(fn, self))

    def recursionindex(self) -> int | None:
        """Return the index of the frame/TracebackEntry where recursion originates if
        appropriate, None if no recursion occurred."""
        cache: dict[tuple[Any, int, int], list[dict[str, Any]]] = {}
        for i, entry in enumerate(self):
            # id for the code.raw is needed to work around
            # the strange metaprogramming in the decorator lib from pypi
            # which generates code objects that have hash/value equality
            # XXX needs a test
            key = entry.frame.code.path, id(entry.frame.code.raw), entry.lineno
            values = cache.setdefault(key, [])
            # Since Python 3.13 f_locals is a proxy, freeze it.
            loc = dict(entry.frame.f_locals)
            if values:
                for otherloc in values:
                    if otherloc == loc:
                        return i
            values.append(loc)
        return None


def stringify_exception(
    exc: BaseException, include_subexception_msg: bool = True
) -> str:
    try:
        notes = getattr(exc, "__notes__", [])
    except KeyError:
        # Workaround for https://github.com/python/cpython/issues/98778 on
        # some 3.10 and 3.11 patch versions.
        HTTPError = getattr(sys.modules.get("urllib.error", None), "HTTPError", ())
        if sys.version_info < (3, 12) and isinstance(exc, HTTPError):
            notes = []
        else:  # pragma: no cover
            # exception not related to above bug, reraise
            raise
    if not include_subexception_msg and isinstance(exc, BaseExceptionGroup):
        message = exc.message
    else:
        message = str(exc)

    return "\n".join(
        [
            message,
            *notes,
        ]
    )


E = TypeVar("E", bound=BaseException, covariant=True)


@final
@dataclasses.dataclass
