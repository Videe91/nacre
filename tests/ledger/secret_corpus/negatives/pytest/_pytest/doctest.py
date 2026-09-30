# mypy: allow-untyped-defs
"""Discover and run doctests in modules and test files."""

from __future__ import annotations

import bdb
from collections.abc import Callable
from collections.abc import Generator
from collections.abc import Iterable
from collections.abc import Sequence
from contextlib import contextmanager
import functools
import inspect
import os
from pathlib import Path
import platform
import re
import sys
import traceback
import types
from typing import Any
from typing import TYPE_CHECKING
import warnings

from _pytest import outcomes
from _pytest._code.code import ExceptionInfo
from _pytest._code.code import ReprFileLocation
from _pytest._code.code import TerminalRepr
from _pytest._io import TerminalWriter
from _pytest.compat import safe_getattr
from _pytest.config import Config
from _pytest.config.argparsing import Parser
from _pytest.fixtures import fixture
from _pytest.fixtures import TopRequest
from _pytest.nodes import Collector
from _pytest.nodes import Item
from _pytest.outcomes import OutcomeException
from _pytest.outcomes import skip
from _pytest.pathlib import fnmatch_ex
from _pytest.python import Module
from _pytest.python_api import approx
from _pytest.warning_types import PytestWarning


if TYPE_CHECKING:
    import doctest

    from typing_extensions import Self

DOCTEST_REPORT_CHOICE_NONE = "none"
DOCTEST_REPORT_CHOICE_CDIFF = "cdiff"
DOCTEST_REPORT_CHOICE_NDIFF = "ndiff"
DOCTEST_REPORT_CHOICE_UDIFF = "udiff"
DOCTEST_REPORT_CHOICE_ONLY_FIRST_FAILURE = "only_first_failure"

DOCTEST_REPORT_CHOICES = (
    DOCTEST_REPORT_CHOICE_NONE,
    DOCTEST_REPORT_CHOICE_CDIFF,
    DOCTEST_REPORT_CHOICE_NDIFF,
    DOCTEST_REPORT_CHOICE_UDIFF,
    DOCTEST_REPORT_CHOICE_ONLY_FIRST_FAILURE,
)

# Lazy definition of runner class
RUNNER_CLASS = None
# Lazy definition of output checker class
CHECKER_CLASS: type[doctest.OutputChecker] | None = None


def pytest_addoption(parser: Parser) -> None:
    parser.addini(
        "doctest_optionflags",
        "Option flags for doctests",
        type="args",
        default=["ELLIPSIS"],
    )
    parser.addini(
        "doctest_encoding", "Encoding used for doctest files", default="utf-8"
    )
    group = parser.getgroup("collect")
    group.addoption(
        "--doctest-modules",
        action="store_true",
        default=False,
        help="Run doctests in all .py modules",
        dest="doctestmodules",
    )
    group.addoption(
        "--doctest-report",
        type=str.lower,
        default="udiff",
        help="Choose another output format for diffs on doctest failure",
        choices=DOCTEST_REPORT_CHOICES,
        dest="doctestreport",
    )
    group.addoption(
        "--doctest-glob",
        action="append",
        default=[],
        metavar="pat",
        help="Doctests file matching pattern, default: test*.txt",
        dest="doctestglob",
    )
    group.addoption(
        "--doctest-ignore-import-errors",
        action="store_true",
        default=False,
        help="Ignore doctest collection errors",
        dest="doctest_ignore_import_errors",
    )
    group.addoption(
        "--doctest-continue-on-failure",
        action="store_true",
        default=False,
        help="For a given doctest, continue to run after the first failure",
        dest="doctest_continue_on_failure",
    )


def pytest_unconfigure() -> None:
    global RUNNER_CLASS

    RUNNER_CLASS = None


def pytest_collect_file(
    file_path: Path,
    parent: Collector,
) -> DoctestModule | DoctestTextfile | None:
    config = parent.config
    if file_path.suffix == ".py":
        if config.option.doctestmodules and not any(
            (_is_setup_py(file_path), _is_main_py(file_path))
        ):
            return DoctestModule.from_parent(parent, path=file_path)
    elif _is_doctest(config, file_path, parent):
        return DoctestTextfile.from_parent(parent, path=file_path)
    return None


def _is_setup_py(path: Path) -> bool:
    if path.name != "setup.py":
        return False
    contents = path.read_bytes()
    return b"setuptools" in contents or b"distutils" in contents


def _is_doctest(config: Config, path: Path, parent: Collector) -> bool:
    if path.suffix in (".txt", ".rst") and parent.session.isinitpath(path):
        return True
    globs = config.getoption("doctestglob") or ["test*.txt"]
    return any(fnmatch_ex(glob, path) for glob in globs)


def _is_main_py(path: Path) -> bool:
    return path.name == "__main__.py"


class ReprFailDoctest(TerminalRepr):
    def __init__(
        self, reprlocation_lines: Sequence[tuple[ReprFileLocation, Sequence[str]]]
    ) -> None:
        self.reprlocation_lines = reprlocation_lines

    def toterminal(self, tw: TerminalWriter) -> None:
        for reprlocation, lines in self.reprlocation_lines:
            for line in lines:
                tw.line(line)
            reprlocation.toterminal(tw)


class MultipleDoctestFailures(Exception):
    def __init__(self, failures: Sequence[doctest.DocTestFailure]) -> None:
        super().__init__()
        self.failures = failures


def _init_runner_class() -> type[doctest.DocTestRunner]:
    import doctest

    class PytestDoctestRunner(doctest.DebugRunner):
        """Runner to collect failures.

        Note that the out variable in this case is a list instead of a
        stdout-like object.
        """

        def __init__(
            self,
            checker: doctest.OutputChecker | None = None,
            verbose: bool | None = None,
            optionflags: int = 0,
            continue_on_failure: bool = True,
        ) -> None:
            super().__init__(checker=checker, verbose=verbose, optionflags=optionflags)
            self.continue_on_failure = continue_on_failure

        def report_failure(
            self,
            out,
            test: doctest.DocTest,
            example: doctest.Example,
            got: str,
        ) -> None:
            failure = doctest.DocTestFailure(test, example, got)
            if self.continue_on_failure:
                out.append(failure)
            else:
                raise failure

        def report_unexpected_exception(
            self,
            out,
            test: doctest.DocTest,
            example: doctest.Example,
            exc_info: tuple[type[BaseException], BaseException, types.TracebackType],
        ) -> None:
            if isinstance(exc_info[1], OutcomeException):
                raise exc_info[1]
            if isinstance(exc_info[1], bdb.BdbQuit):
                outcomes.exit("Quitting debugger")
            failure = doctest.UnexpectedException(test, example, exc_info)
            if self.continue_on_failure:
                out.append(failure)
            else:
                raise failure

    return PytestDoctestRunner


def _get_runner(
    checker: doctest.OutputChecker | None = None,
    verbose: bool | None = None,
    optionflags: int = 0,
    continue_on_failure: bool = True,
) -> doctest.DocTestRunner:
    # We need this in order to do a lazy import on doctest
    global RUNNER_CLASS
    if RUNNER_CLASS is None:
        RUNNER_CLASS = _init_runner_class()
    # Type ignored because the continue_on_failure argument is only defined on
    # PytestDoctestRunner, which is lazily defined so can't be used as a type.
    return RUNNER_CLASS(  # type: ignore
        checker=checker,
        verbose=verbose,
        optionflags=optionflags,
        continue_on_failure=continue_on_failure,
    )


class DoctestItem(Item):
    def __init__(
        self,
        name: str,
        parent: DoctestTextfile | DoctestModule,
        runner: doctest.DocTestRunner,
        dtest: doctest.DocTest,
    ) -> None:
        super().__init__(name, parent)
        self.runner = runner
        self.dtest = dtest

        # Stuff needed for fixture support.
        self.obj = None
        fm = self.session._fixturemanager
        fixtureinfo = fm.getfixtureinfo(node=self, func=None, cls=None)
        self._fixtureinfo = fixtureinfo
        self.fixturenames = fixtureinfo.names_closure
        self._initrequest()

    @classmethod
    def from_parent(  # type: ignore[override]
        cls,
        parent: DoctestTextfile | DoctestModule,
        *,
        name: str,
        runner: doctest.DocTestRunner,
        dtest: doctest.DocTest,
    ) -> Self:
        # incompatible signature due to imposed limits on subclass
        """The public named constructor."""
        return super().from_parent(name=name, parent=parent, runner=runner, dtest=dtest)

    def _initrequest(self) -> None:
        self.funcargs: dict[str, object] = {}
        self._request = TopRequest(self, _ispytest=True)  # type: ignore[arg-type]

    def setup(self) -> None:
        self._request._fillfixtures()
        globs = dict(getfixture=self._request.getfixturevalue)
        for name, value in self._request.getfixturevalue("doctest_namespace").items():
            globs[name] = value
        self.dtest.globs.update(globs)

    def runtest(self) -> None:
        _check_all_skipped(self.dtest)
        self._disable_output_capturing_for_darwin()
        failures: list[doctest.DocTestFailure] = []
        # Type ignored because we change the type of `out` from what
        # doctest expects.
        self.runner.run(self.dtest, out=failures)  # type: ignore[arg-type]
        if failures:
            raise MultipleDoctestFailures(failures)

    def _disable_output_capturing_for_darwin(self) -> None:
        """Disable output capturing. Otherwise, stdout is lost to doctest (#985)."""
        if platform.system() != "Darwin":
            return
        capman = self.config.pluginmanager.getplugin("capturemanager")
        if capman:
            capman.suspend_global_capture(in_=True)
            out, err = capman.read_global_capture()
            sys.stdout.write(out)
            sys.stderr.write(err)

    # TODO: Type ignored -- breaks Liskov Substitution.
    def repr_failure(  # type: ignore[override]
        self,
        excinfo: ExceptionInfo[BaseException],
    ) -> str | TerminalRepr:
        import doctest

        failures: (
            Sequence[doctest.DocTestFailure | doctest.UnexpectedException] | None
        ) = None
        if isinstance(
            excinfo.value, doctest.DocTestFailure | doctest.UnexpectedException
        ):
            failures = [excinfo.value]
        elif isinstance(excinfo.value, MultipleDoctestFailures):
            failures = excinfo.value.failures

        if failures is None:
            return super().repr_failure(excinfo)

        reprlocation_lines = []
        for failure in failures:
            example = failure.example
            test = failure.test
            filename = test.filename
            if test.lineno is None:
                lineno = None
            else:
                lineno = test.lineno + example.lineno + 1
            message = type(failure).__name__
            # TODO: ReprFileLocation doesn't expect a None lineno.
            reprlocation = ReprFileLocation(filename, lineno, message)  # type: ignore[arg-type]
            checker = _get_checker()
            report_choice = _get_report_choice(self.config.getoption("doctestreport"))
            if lineno is not None:
                assert failure.test.docstring is not None
                lines = failure.test.docstring.splitlines(False)
                # add line numbers to the left of the error message
                assert test.lineno is not None
                lines = [
                    f"{i + test.lineno + 1:03d} {x}" for (i, x) in enumerate(lines)
                ]
                # trim docstring error lines to 10
                lines = lines[max(example.lineno - 9, 0) : example.lineno + 1]
            else:
                lines = [
                    "EXAMPLE LOCATION UNKNOWN, not showing all tests of that example"
                ]
                indent = ">>>"
                for line in example.source.splitlines():
                    lines.append(f"??? {indent} {line}")
                    indent = "..."
            if isinstance(failure, doctest.DocTestFailure):
                lines += checker.output_difference(
                    example, failure.got, report_choice
                ).split("\n")
            else:
                inner_excinfo = ExceptionInfo.from_exc_info(failure.exc_info)
                lines += [f"UNEXPECTED EXCEPTION: {inner_excinfo.value!r}"]
                lines += [
                    x.strip("\n") for x in traceback.format_exception(*failure.exc_info)
                ]
            reprlocation_lines.append((reprlocation, lines))
        return ReprFailDoctest(reprlocation_lines)

    def reportinfo(self) -> tuple[os.PathLike[str] | str, int | None, str]:
        return self.path, self.dtest.lineno, f"[doctest] {self.name}"


def _get_flag_lookup() -> dict[str, int]:
    import doctest

    return dict(
        DONT_ACCEPT_TRUE_FOR_1=doctest.DONT_ACCEPT_TRUE_FOR_1,
        DONT_ACCEPT_BLANKLINE=doctest.DONT_ACCEPT_BLANKLINE,
        NORMALIZE_WHITESPACE=doctest.NORMALIZE_WHITESPACE,
        ELLIPSIS=doctest.ELLIPSIS,
        IGNORE_EXCEPTION_DETAIL=doctest.IGNORE_EXCEPTION_DETAIL,
        COMPARISON_FLAGS=doctest.COMPARISON_FLAGS,
        ALLOW_UNICODE=_get_allow_unicode_flag(),
        ALLOW_BYTES=_get_allow_bytes_flag(),
        NUMBER=_get_number_flag(),
    )


def get_optionflags(config: Config) -> int:
    optionflags_str = config.getini("doctest_optionflags")
    flag_lookup_table = _get_flag_lookup()
    flag_acc = 0
    for flag in optionflags_str:
        flag_acc |= flag_lookup_table[flag]
    return flag_acc


def _get_continue_on_failure(config: Config) -> bool:
    continue_on_failure: bool = config.getvalue("doctest_continue_on_failure")
    if continue_on_failure:
        # We need to turn off this if we use pdb since we should stop at
        # the first failure.
        if config.getvalue("usepdb"):
            continue_on_failure = False
    return continue_on_failure


class DoctestTextfile(Module):
    obj = None

    def collect(self) -> Iterable[DoctestItem]:
        import doctest

        # Inspired by doctest.testfile; ideally we would use it directly,
        # but it doesn't support passing a custom checker.
        encoding = self.config.getini("doctest_encoding")
        text = self.path.read_text(encoding)
        filename = str(self.path)
        name = self.path.name
        globs = {"__name__": "__main__"}

        optionflags = get_optionflags(self.config)

        runner = _get_runner(
            verbose=False,
            optionflags=optionflags,
            checker=_get_checker(),
            continue_on_failure=_get_continue_on_failure(self.config),
        )

        parser = doctest.DocTestParser()
        test = parser.get_doctest(text, globs, name, filename, 0)
        if test.examples:
            yield DoctestItem.from_parent(
                self, name=test.name, runner=runner, dtest=test
            )


def _check_all_skipped(test: doctest.DocTest) -> None:
    """Raise pytest.skip() if all examples in the given DocTest have the SKIP
    option set."""
    import doctest

    all_skipped = all(x.options.get(doctest.SKIP, False) for x in test.examples)
    if all_skipped:
        skip("all tests skipped by +SKIP option")


def _is_mocked(obj: object) -> bool:
    """Return if an object is possibly a mock object by checking the
    existence of a highly improbable attribute."""
    return (
        safe_getattr(obj, "pytest_mock_example_attribute_that_shouldnt_exist", None)
        is not None
    )


@contextmanager
def _patch_unwrap_mock_aware() -> Generator[None]:
    """Context manager which replaces ``inspect.unwrap`` with a version
    that's aware of mock objects and doesn't recurse into them."""
    real_unwrap = inspect.unwrap

    def _mock_aware_unwrap(
        func: Callable[..., Any], *, stop: Callable[[Any], Any] | None = None
    ) -> Any:
        try:
            if stop is None or stop is _is_mocked:
                return real_unwrap(func, stop=_is_mocked)
            _stop = stop
            return real_unwrap(func, stop=lambda obj: _is_mocked(obj) or _stop(func))
        except Exception as e:
            warnings.warn(
                f"Got {e!r} when unwrapping {func!r}.  This is usually caused "
