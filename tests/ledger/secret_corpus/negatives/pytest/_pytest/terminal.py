# mypy: allow-untyped-defs
"""Terminal reporting of the full testing process.

This is a good source for looking at the various reporting hooks.
"""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Callable
from collections.abc import Generator
from collections.abc import Mapping
from collections.abc import Sequence
import dataclasses
import datetime
from functools import partial
import inspect
from pathlib import Path
import platform
import sys
import textwrap
from typing import Any
from typing import ClassVar
from typing import final
from typing import Literal
from typing import NamedTuple
from typing import TextIO
from typing import TYPE_CHECKING
import warnings

import pluggy

from _pytest import compat
from _pytest import nodes
from _pytest import timing
from _pytest._code import ExceptionInfo
from _pytest._code.code import ExceptionRepr
from _pytest._io import TerminalWriter
from _pytest._io.wcwidth import wcswidth
import _pytest._version
from _pytest.compat import running_on_ci
from _pytest.config import _PluggyPlugin
from _pytest.config import Config
from _pytest.config import ExitCode
from _pytest.config import hookimpl
from _pytest.config.argparsing import Parser
from _pytest.nodes import Item
from _pytest.nodes import Node
from _pytest.pathlib import absolutepath
from _pytest.pathlib import bestrelpath
from _pytest.reports import BaseReport
from _pytest.reports import CollectReport
from _pytest.reports import TestReport


if TYPE_CHECKING:
    from _pytest.main import Session


REPORT_COLLECTING_RESOLUTION = 0.5

KNOWN_TYPES = (
    "failed",
    "passed",
    "skipped",
    "deselected",
    "xfailed",
    "xpassed",
    "warnings",
    "error",
    "subtests passed",
    "subtests failed",
    "subtests skipped",
)

_REPORTCHARS_DEFAULT = "fE"


class MoreQuietAction(argparse.Action):
    """A modified copy of the argparse count action which counts down and updates
    the legacy quiet attribute at the same time.

    Used to unify verbosity handling.
    """

    def __init__(
        self,
        option_strings: Sequence[str],
        dest: str,
        default: object = None,
        required: bool = False,
        help: str | None = None,
    ) -> None:
        super().__init__(
            option_strings=option_strings,
            dest=dest,
            nargs=0,
            default=default,
            required=required,
            help=help,
        )

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str | Sequence[object] | None,
        option_string: str | None = None,
    ) -> None:
        new_count = getattr(namespace, self.dest, 0) - 1
        setattr(namespace, self.dest, new_count)
        # todo Deprecate config.quiet
        namespace.quiet = getattr(namespace, "quiet", 0) + 1


class TestShortLogReport(NamedTuple):
    """Used to store the test status result category, shortletter and verbose word.
    For example ``"rerun", "R", ("RERUN", {"yellow": True})``.

    :ivar category:
        The class of result, for example ``“passed”``, ``“skipped”``, ``“error”``, or the empty string.

    :ivar letter:
        The short letter shown as testing progresses, for example ``"."``, ``"s"``, ``"E"``, or the empty string.

    :ivar word:
        Verbose word is shown as testing progresses in verbose mode, for example ``"PASSED"``, ``"SKIPPED"``,
        ``"ERROR"``, or the empty string.
    """

    category: str
    letter: str
    word: str | tuple[str, Mapping[str, bool]]


def pytest_addoption(parser: Parser) -> None:
    group = parser.getgroup("terminal reporting", "Reporting", after="general")
    group._addoption(  # private to use reserved lower-case short option
        "-v",
        "--verbose",
        action="count",
        default=0,
        dest="verbose",
        help="Increase verbosity",
    )
    group.addoption(
        "--no-header",
        action="store_true",
        default=False,
        dest="no_header",
        help="Disable header",
    )
    group.addoption(
        "--no-summary",
        action="store_true",
        default=False,
        dest="no_summary",
        help="Disable summary",
    )
    group.addoption(
        "--no-fold-skipped",
        action="store_false",
        dest="fold_skipped",
        default=True,
        help="Do not fold skipped tests in short summary.",
    )
    group.addoption(
        "--force-short-summary",
        action="store_true",
        dest="force_short_summary",
        default=False,
        help="Force condensed summary output regardless of verbosity level.",
    )
    group._addoption(  # private to use reserved lower-case short option
        "-q",
        "--quiet",
        action=MoreQuietAction,
        default=0,
        dest="verbose",
        help="Decrease verbosity",
    )
    group.addoption(
        "--verbosity",
        dest="verbose",
        type=int,
        default=0,
        help="Set verbosity. Default: 0.",
    )
    group._addoption(  # private to use reserved lower-case short option
        "-r",
        "--report-chars",
        action="store",
        dest="reportchars",
        default=_REPORTCHARS_DEFAULT,
        metavar="chars",
        help="Show extra test summary info as specified by chars: (f)ailed, "
        "(E)rror, (s)kipped, (x)failed, (X)passed, "
        "(p)assed, (P)assed with output, (a)ll except passed (p/P), or (A)ll. "
        "(w)arnings are enabled by default (see --disable-warnings), "
        "'N' can be used to reset the list. (default: 'fE').",
    )
    group.addoption(
        "--disable-warnings",
        "--disable-pytest-warnings",
        default=False,
        dest="disable_warnings",
        action="store_true",
        help="Disable warnings summary",
    )
    group._addoption(  # private to use reserved lower-case short option
        "-l",
        "--showlocals",
        action="store_true",
        dest="showlocals",
        default=False,
        help="Show locals in tracebacks (disabled by default)",
    )
    group.addoption(
        "--no-showlocals",
        action="store_false",
        dest="showlocals",
        help="Hide locals in tracebacks (negate --showlocals passed through addopts)",
    )
    group.addoption(
        "--tb",
        metavar="style",
        action="store",
        dest="tbstyle",
        default="auto",
        choices=["auto", "long", "short", "no", "line", "native"],
        help="Traceback print mode (auto/long/short/line/native/no)",
    )
    group.addoption(
        "--xfail-tb",
        action="store_true",
        dest="xfail_tb",
        default=False,
        help="Show tracebacks for xfail (as long as --tb != no)",
    )
    group.addoption(
        "--show-capture",
        action="store",
        dest="showcapture",
        choices=["no", "stdout", "stderr", "log", "all"],
        default="all",
        help="Controls how captured stdout/stderr/log is shown on failed tests. "
        "Default: all.",
    )
    group.addoption(
        "--fulltrace",
        "--full-trace",
        action="store_true",
        default=False,
        help="Don't cut any tracebacks (default is to cut)",
    )
    group.addoption(
        "--color",
        metavar="color",
        action="store",
        dest="color",
        default="auto",
        choices=["yes", "no", "auto"],
        help="Color terminal output (yes/no/auto)",
    )
    group.addoption(
        "--code-highlight",
        default="yes",
        choices=["yes", "no"],
        help="Whether code should be highlighted (only if --color is also enabled). "
        "Default: yes.",
    )

    parser.addini(
        "console_output_style",
        help='Console output: "classic", or with additional progress information '
        '("progress" (percentage) | "count" | "progress-even-when-capture-no" (forces '
        "progress even when capture=no)",
        default="progress",
    )
    Config._add_verbosity_ini(
        parser,
        Config.VERBOSITY_TEST_CASES,
        help=(
            "Specify a verbosity level for test case execution, overriding the main level. "
            "Higher levels will provide more detailed information about each test case executed."
        ),
    )


def pytest_configure(config: Config) -> None:
    reporter = TerminalReporter(config, sys.stdout)
    config.pluginmanager.register(reporter, "terminalreporter")
    if config.option.debug or config.option.traceconfig:

        def mywriter(tags, args):
            msg = " ".join(map(str, args))
            reporter.write_line("[traceconfig] " + msg)

        config.trace.root.setprocessor("pytest:config", mywriter)

    # See terminalprogress.py.
    # On Windows it's safe to load by default.
    if sys.platform == "win32":
        config.pluginmanager.import_plugin("terminalprogress")


def getreportopt(config: Config) -> str:
    reportchars: str = config.option.reportchars

    old_aliases = {"F", "S"}
    reportopts = ""
    for char in reportchars:
        if char in old_aliases:
            char = char.lower()
        if char == "a":
            reportopts = "sxXEf"
        elif char == "A":
            reportopts = "PpsxXEf"
        elif char == "N":
            reportopts = ""
        elif char not in reportopts:
            reportopts += char

    if not config.option.disable_warnings and "w" not in reportopts:
        reportopts = "w" + reportopts
    elif config.option.disable_warnings and "w" in reportopts:
        reportopts = reportopts.replace("w", "")

    return reportopts


@hookimpl(trylast=True)  # after _pytest.runner
def pytest_report_teststatus(report: BaseReport) -> tuple[str, str, str]:
    letter = "F"
    if report.passed:
        letter = "."
    elif report.skipped:
        letter = "s"

    outcome: str = report.outcome
    if report.when in ("collect", "setup", "teardown") and outcome == "failed":
        outcome = "error"
        letter = "E"

    return outcome, letter, outcome.upper()


@dataclasses.dataclass
class WarningReport:
    """Simple structure to hold warnings information captured by ``pytest_warning_recorded``.

    :ivar str message:
        User friendly message about the warning.
    :ivar str|None nodeid:
        nodeid that generated the warning (see ``get_location``).
    :ivar tuple fslocation:
        File system location of the source of the warning (see ``get_location``).
    """

    message: str
    nodeid: str | None = None
    fslocation: tuple[str, int] | None = None

    count_towards_summary: ClassVar = True

    def get_location(self, config: Config) -> str | None:
        """Return the more user-friendly information about the location of a warning, or None."""
        if self.nodeid:
            return self.nodeid
        if self.fslocation:
            filename, linenum = self.fslocation
            relpath = bestrelpath(config.invocation_params.dir, absolutepath(filename))
            return f"{relpath}:{linenum}"
        return None


@final
class TerminalReporter:
    def __init__(self, config: Config, file: TextIO | None = None) -> None:
        import _pytest.config

        self.config = config
        self._numcollected = 0
        self._session: Session | None = None
        self._showfspath: bool | None = None

        self.stats: dict[str, list[Any]] = {}
        self._main_color: str | None = None
        self._known_types: list[str] | None = None
        self.startpath = config.invocation_params.dir
        if file is None:
            file = sys.stdout
        self._tw = _pytest.config.create_terminal_writer(config, file)
        self._screen_width = self._tw.fullwidth
        self.currentfspath: None | Path | str | int = None
        self.reportchars = getreportopt(config)
        self.foldskipped = config.option.fold_skipped
        self.hasmarkup = self._tw.hasmarkup
        # isatty should be a method but was wrongly implemented as a boolean.
        # We use CallableBool here to support both.
        self.isatty = compat.CallableBool(file.isatty())
        self._progress_nodeids_reported: set[str] = set()
        self._timing_nodeids_reported: set[str] = set()
        self._show_progress_info = self._determine_show_progress_info()
        self._collect_report_last_write = timing.Instant()
        self._already_displayed_warnings: int | None = None
        self._keyboardinterrupt_memo: ExceptionRepr | None = None

    def _determine_show_progress_info(
        self,
    ) -> Literal["progress", "count", "times", False]:
        """Return whether we should display progress information based on the current config."""
        # do not show progress if we are not capturing output (#3038) unless explicitly
        # overridden by progress-even-when-capture-no
        if (
            self.config.getoption("capture", "no") == "no"
            and self.config.getini("console_output_style")
            != "progress-even-when-capture-no"
        ):
            return False
        # do not show progress if we are showing fixture setup/teardown
        if self.config.getoption("setupshow", False):
            return False
        cfg: str = self.config.getini("console_output_style")
        if cfg in {"progress", "progress-even-when-capture-no"}:
            return "progress"
        elif cfg == "count":
            return "count"
        elif cfg == "times":
            return "times"
        else:
            return False

    @property
    def verbosity(self) -> int:
        verbosity: int = self.config.option.verbose
        return verbosity

    @property
    def showheader(self) -> bool:
        return self.verbosity >= 0

    @property
    def no_header(self) -> bool:
        return bool(self.config.option.no_header)

    @property
    def no_summary(self) -> bool:
        return bool(self.config.option.no_summary)

    @property
    def showfspath(self) -> bool:
        if self._showfspath is None:
            return self.config.get_verbosity(Config.VERBOSITY_TEST_CASES) >= 0
        return self._showfspath

    @showfspath.setter
    def showfspath(self, value: bool | None) -> None:
        self._showfspath = value

    @property
    def showlongtestinfo(self) -> bool:
        return self.config.get_verbosity(Config.VERBOSITY_TEST_CASES) > 0

    @property
    def reported_progress(self) -> int:
        """The amount of items reported in the progress so far.

        :meta private:
        """
        return len(self._progress_nodeids_reported)

    def hasopt(self, char: str) -> bool:
        char = {"xfailed": "x", "skipped": "s"}.get(char, char)
        return char in self.reportchars

    def write_fspath_result(self, nodeid: str, res: str, **markup: bool) -> None:
        fspath = self.config.rootpath / nodeid.split("::", maxsplit=1)[0]
        if self.currentfspath is None or fspath != self.currentfspath:
            if self.currentfspath is not None and self._show_progress_info:
                self._write_progress_information_filling_space()
            self.currentfspath = fspath
            relfspath = bestrelpath(self.startpath, fspath)
            self._tw.line()
            self._tw.write(relfspath + " ")
        self._tw.write(res, flush=True, **markup)

    def write_ensure_prefix(self, prefix: str, extra: str = "", **kwargs) -> None:
        if self.currentfspath != prefix:
            self._tw.line()
            self.currentfspath = prefix
            self._tw.write(prefix)
        if extra:
            self._tw.write(extra, **kwargs)
            self.currentfspath = -2

    def ensure_newline(self) -> None:
        if self.currentfspath:
            self._tw.line()
            self.currentfspath = None

    def wrap_write(
        self,
        content: str,
        *,
        flush: bool = False,
        margin: int = 8,
        line_sep: str = "\n",
        **markup: bool,
    ) -> None:
        """Wrap message with margin for progress info."""
        width_of_current_line = self._tw.width_of_current_line
        wrapped = line_sep.join(
            textwrap.wrap(
                " " * width_of_current_line + content,
                width=self._screen_width - margin,
                drop_whitespace=True,
                replace_whitespace=False,
            ),
        )
