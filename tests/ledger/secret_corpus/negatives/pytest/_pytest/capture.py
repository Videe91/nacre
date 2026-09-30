# mypy: allow-untyped-defs
"""Per-test stdout/stderr capturing mechanism."""

from __future__ import annotations

import abc
import collections
from collections.abc import Generator
from collections.abc import Iterable
from collections.abc import Iterator
import contextlib
import io
from io import UnsupportedOperation
import os
import sys
from tempfile import TemporaryFile
from types import TracebackType
from typing import Any
from typing import AnyStr
from typing import BinaryIO
from typing import cast
from typing import Final
from typing import final
from typing import Generic
from typing import Literal
from typing import NamedTuple
from typing import TextIO
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from typing_extensions import Self

from _pytest.config import Config
from _pytest.config import hookimpl
from _pytest.config.argparsing import Parser
from _pytest.deprecated import check_ispytest
from _pytest.fixtures import fixture
from _pytest.fixtures import SubRequest
from _pytest.nodes import Collector
from _pytest.nodes import File
from _pytest.nodes import Item
from _pytest.reports import CollectReport


_CaptureMethod = Literal["fd", "sys", "no", "tee-sys"]


def pytest_addoption(parser: Parser) -> None:
    group = parser.getgroup("general")
    group.addoption(
        "--capture",
        action="store",
        default="fd",
        metavar="method",
        choices=["fd", "sys", "no", "tee-sys"],
        help="Per-test capturing method: one of fd|sys|no|tee-sys",
    )
    group._addoption(  # private to use reserved lower-case short option
        "-s",
        action="store_const",
        const="no",
        dest="capture",
        help="Shortcut for --capture=no",
    )


def _colorama_workaround() -> None:
    """Ensure colorama is imported so that it attaches to the correct stdio
    handles on Windows.

    colorama uses the terminal on import time. So if something does the
    first import of colorama while I/O capture is active, colorama will
    fail in various ways.
    """
    if sys.platform.startswith("win32"):
        try:
            import colorama  # noqa: F401
        except ImportError:
            pass


def _readline_workaround() -> None:
    """Ensure readline is imported early so it attaches to the correct stdio handles.

    This isn't a problem with the default GNU readline implementation, but in
    some configurations, Python uses libedit instead (on macOS, and for prebuilt
    binaries such as used by uv).

    In theory this is only needed if readline.backend == "libedit", but the
    workaround consists of importing readline here, so we already worked around
    the issue by the time we could check if we need to.
    """
    try:
        import readline  # noqa: F401
    except ImportError:
        pass


def _windowsconsoleio_workaround(stream: TextIO) -> None:
    """Workaround for Windows Unicode console handling.

    Python 3.6 implemented Unicode console handling for Windows. This works
    by reading/writing to the raw console handle using
    ``{Read,Write}ConsoleW``.

    The problem is that we are going to ``dup2`` over the stdio file
    descriptors when doing ``FDCapture`` and this will ``CloseHandle`` the
    handles used by Python to write to the console. Though there is still some
    weirdness and the console handle seems to only be closed randomly and not
    on the first call to ``CloseHandle``, or maybe it gets reopened with the
    same handle value when we suspend capturing.

    The workaround in this case will reopen stdio with a different fd which
    also means a different handle by replicating the logic in
    "Py_lifecycle.c:initstdio/create_stdio".

    :param stream:
        In practice ``sys.stdout`` or ``sys.stderr``, but given
        here as parameter for unittesting purposes.

    See https://github.com/pytest-dev/py/issues/103.
    """
    if not sys.platform.startswith("win32") or hasattr(sys, "pypy_version_info"):
        return

    # Bail out if ``stream`` doesn't seem like a proper ``io`` stream (#2666).
    if not hasattr(stream, "buffer"):  # type: ignore[unreachable,unused-ignore]
        return

    raw_stdout = stream.buffer.raw if hasattr(stream.buffer, "raw") else stream.buffer

    if not isinstance(raw_stdout, io._WindowsConsoleIO):  # type: ignore[attr-defined,unused-ignore]
        return

    def _reopen_stdio(f, mode):
        if not hasattr(stream.buffer, "raw") and mode[0] == "w":
            buffering = 0
        else:
            buffering = -1

        return io.TextIOWrapper(
            open(os.dup(f.fileno()), mode, buffering),
            f.encoding,
            f.errors,
            f.newlines,
            f.line_buffering,
        )

    sys.stdin = _reopen_stdio(sys.stdin, "rb")
    sys.stdout = _reopen_stdio(sys.stdout, "wb")
    sys.stderr = _reopen_stdio(sys.stderr, "wb")


@hookimpl(wrapper=True)
def pytest_load_initial_conftests(early_config: Config) -> Generator[None]:
    ns = early_config.known_args_namespace
    if ns.capture == "fd":
        _windowsconsoleio_workaround(sys.stdout)
    _colorama_workaround()
    _readline_workaround()
    pluginmanager = early_config.pluginmanager
    capman = CaptureManager(ns.capture)
    pluginmanager.register(capman, "capturemanager")

    # Make sure that capturemanager is properly reset at final shutdown.
    early_config.add_cleanup(capman.stop_global_capturing)

    # Finally trigger conftest loading but while capturing (issue #93).
    capman.start_global_capturing()
    try:
        try:
            yield
        finally:
            capman.suspend_global_capture()
    except BaseException:
        out, err = capman.read_global_capture()
        sys.stdout.write(out)
        sys.stderr.write(err)
        raise


# IO Helpers.


class EncodedFile(io.TextIOWrapper):
    __slots__ = ()

    @property
    def name(self) -> str:
        # Ensure that file.name is a string. Workaround for a Python bug
        # fixed in >=3.7.4: https://bugs.python.org/issue36015
        return repr(self.buffer)

    @property
    def mode(self) -> str:
        # TextIOWrapper doesn't expose a mode, but at least some of our
        # tests check it.
        assert hasattr(self.buffer, "mode")
        return cast(str, self.buffer.mode.replace("b", ""))


class CaptureIO(io.TextIOWrapper):
    def __init__(self) -> None:
        super().__init__(io.BytesIO(), encoding="UTF-8", newline="", write_through=True)

    def getvalue(self) -> str:
        assert isinstance(self.buffer, io.BytesIO)
        return self.buffer.getvalue().decode("UTF-8")


class TeeCaptureIO(CaptureIO):
    def __init__(self, other: TextIO) -> None:
        self._other = other
        super().__init__()

    def write(self, s: str) -> int:
        super().write(s)
        return self._other.write(s)


class DontReadFromInput(TextIO):
    @property
    def encoding(self) -> str:
        assert sys.__stdin__ is not None
        return sys.__stdin__.encoding

    def read(self, size: int = -1) -> str:
        raise OSError(
            "pytest: reading from stdin while output is captured!  Consider using `-s`."
        )

    readline = read

    def __next__(self) -> str:
        return self.readline()

    def readlines(self, hint: int | None = -1) -> list[str]:
        raise OSError(
            "pytest: reading from stdin while output is captured!  Consider using `-s`."
        )

    def __iter__(self) -> Iterator[str]:
        return self

    def fileno(self) -> int:
        raise UnsupportedOperation("redirected stdin is pseudofile, has no fileno()")

    def flush(self) -> None:
        raise UnsupportedOperation("redirected stdin is pseudofile, has no flush()")

    def isatty(self) -> bool:
        return False

    def close(self) -> None:
        pass

    def readable(self) -> bool:
        return False

    def seek(self, offset: int, whence: int = 0) -> int:
        raise UnsupportedOperation("redirected stdin is pseudofile, has no seek(int)")

    def seekable(self) -> bool:
        return False

    def tell(self) -> int:
        raise UnsupportedOperation("redirected stdin is pseudofile, has no tell()")

    def truncate(self, size: int | None = None) -> int:
        raise UnsupportedOperation("cannot truncate stdin")

    def write(self, data: str) -> int:
        raise UnsupportedOperation("cannot write to stdin")

    def writelines(self, lines: Iterable[str]) -> None:
        raise UnsupportedOperation("Cannot write to stdin")

    def writable(self) -> bool:
        return False

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        type: type[BaseException] | None,
        value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        pass

    @property
    def buffer(self) -> BinaryIO:
        # The str/bytes doesn't actually matter in this type, so OK to fake.
        return self  # type: ignore[return-value]


# Capture classes.


class CaptureBase(abc.ABC, Generic[AnyStr]):
    EMPTY_BUFFER: AnyStr

    @abc.abstractmethod
    def __init__(self, fd: int) -> None:
        raise NotImplementedError()

    @abc.abstractmethod
    def start(self) -> None:
        raise NotImplementedError()

    @abc.abstractmethod
    def done(self) -> None:
        raise NotImplementedError()

    @abc.abstractmethod
    def suspend(self) -> None:
        raise NotImplementedError()

    @abc.abstractmethod
    def resume(self) -> None:
        raise NotImplementedError()

    @abc.abstractmethod
    def writeorg(self, data: AnyStr) -> None:
        raise NotImplementedError()

    @abc.abstractmethod
    def snap(self) -> AnyStr:
        raise NotImplementedError()


patchsysdict = {0: "stdin", 1: "stdout", 2: "stderr"}


class NoCapture(CaptureBase[str]):
    EMPTY_BUFFER = ""

    def __init__(self, fd: int) -> None:
        pass

    def start(self) -> None:
        pass

    def done(self) -> None:
        pass

    def suspend(self) -> None:
        pass

    def resume(self) -> None:
        pass

    def snap(self) -> str:
        return ""

    def writeorg(self, data: str) -> None:
        pass


class SysCaptureBase(CaptureBase[AnyStr]):
    def __init__(
        self, fd: int, tmpfile: TextIO | None = None, *, tee: bool = False
    ) -> None:
        name = patchsysdict[fd]
        self._old: TextIO = getattr(sys, name)
        self.name = name
        if tmpfile is None:
            if name == "stdin":
                tmpfile = DontReadFromInput()
            else:
                tmpfile = CaptureIO() if not tee else TeeCaptureIO(self._old)
        self.tmpfile = tmpfile
        self._state = "initialized"

    def repr(self, class_name: str) -> str:
        return "<{} {} _old={} _state={!r} tmpfile={!r}>".format(
            class_name,
            self.name,
            (hasattr(self, "_old") and repr(self._old)) or "<UNSET>",
            self._state,
            self.tmpfile,
        )

    def __repr__(self) -> str:
        return "<{} {} _old={} _state={!r} tmpfile={!r}>".format(
            self.__class__.__name__,
            self.name,
            (hasattr(self, "_old") and repr(self._old)) or "<UNSET>",
            self._state,
            self.tmpfile,
        )

    def _assert_state(self, op: str, states: tuple[str, ...]) -> None:
        assert self._state in states, (
            "cannot {} in state {!r}: expected one of {}".format(
                op, self._state, ", ".join(states)
            )
        )

    def start(self) -> None:
        self._assert_state("start", ("initialized",))
        setattr(sys, self.name, self.tmpfile)
        self._state = "started"

    def done(self) -> None:
        self._assert_state("done", ("initialized", "started", "suspended", "done"))
        if self._state == "done":
            return
        setattr(sys, self.name, self._old)
        del self._old
        self.tmpfile.close()
        self._state = "done"

    def suspend(self) -> None:
        self._assert_state("suspend", ("started", "suspended"))
        setattr(sys, self.name, self._old)
        self._state = "suspended"

    def resume(self) -> None:
        self._assert_state("resume", ("started", "suspended"))
        if self._state == "started":
            return
        setattr(sys, self.name, self.tmpfile)
        self._state = "started"


class SysCaptureBinary(SysCaptureBase[bytes]):
    EMPTY_BUFFER = b""

    def snap(self) -> bytes:
        self._assert_state("snap", ("started", "suspended"))
        self.tmpfile.seek(0)
        res = self.tmpfile.buffer.read()
        self.tmpfile.seek(0)
        self.tmpfile.truncate()
        return res

    def writeorg(self, data: bytes) -> None:
        self._assert_state("writeorg", ("started", "suspended"))
        self._old.flush()
        self._old.buffer.write(data)
        self._old.buffer.flush()


class SysCapture(SysCaptureBase[str]):
    EMPTY_BUFFER = ""

    def snap(self) -> str:
        self._assert_state("snap", ("started", "suspended"))
        assert isinstance(self.tmpfile, CaptureIO)
        res = self.tmpfile.getvalue()
        self.tmpfile.seek(0)
        self.tmpfile.truncate()
        return res

    def writeorg(self, data: str) -> None:
        self._assert_state("writeorg", ("started", "suspended"))
        self._old.write(data)
        self._old.flush()


class FDCaptureBase(CaptureBase[AnyStr]):
    def __init__(self, targetfd: int) -> None:
        self.targetfd = targetfd

        try:
            os.fstat(targetfd)
        except OSError:
            # FD capturing is conceptually simple -- create a temporary file,
            # redirect the FD to it, redirect back when done. But when the
            # target FD is invalid it throws a wrench into this lovely scheme.
            #
            # Tests themselves shouldn't care if the FD is valid, FD capturing
            # should work regardless of external circumstances. So falling back
            # to just sys capturing is not a good option.
            #
            # Further complications are the need to support suspend() and the
            # possibility of FD reuse (e.g. the tmpfile getting the very same
            # target FD). The following approach is robust, I believe.
            self.targetfd_invalid: int | None = os.open(os.devnull, os.O_RDWR)
            os.dup2(self.targetfd_invalid, targetfd)
        else:
            self.targetfd_invalid = None
        self.targetfd_save = os.dup(targetfd)

        if targetfd == 0:
            self.tmpfile = open(os.devnull, encoding="utf-8")
            self.syscapture: CaptureBase[str] = SysCapture(targetfd)
        else:
            self.tmpfile = EncodedFile(
                TemporaryFile(buffering=0),
                encoding="utf-8",
                errors="replace",
                newline="",
                write_through=True,
            )
            if targetfd in patchsysdict:
                self.syscapture = SysCapture(targetfd, self.tmpfile)
            else:
                self.syscapture = NoCapture(targetfd)

        self._state = "initialized"

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__} {self.targetfd} oldfd={self.targetfd_save} "
            f"_state={self._state!r} tmpfile={self.tmpfile!r}>"
        )

    def _assert_state(self, op: str, states: tuple[str, ...]) -> None:
        assert self._state in states, (
            "cannot {} in state {!r}: expected one of {}".format(
                op, self._state, ", ".join(states)
            )
        )

    def start(self) -> None:
        """Start capturing on targetfd using memorized tmpfile."""
        self._assert_state("start", ("initialized",))
        os.dup2(self.tmpfile.fileno(), self.targetfd)
        self.syscapture.start()
        self._state = "started"

    def done(self) -> None:
        """Stop capturing, restore streams, return original capture file,
        seeked to position zero."""
        self._assert_state("done", ("initialized", "started", "suspended", "done"))
        if self._state == "done":
            return
        os.dup2(self.targetfd_save, self.targetfd)
        os.close(self.targetfd_save)
        if self.targetfd_invalid is not None:
            if self.targetfd_invalid != self.targetfd:
