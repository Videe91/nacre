# mypy: allow-untyped-defs
"""Command line options, config-file and conftest.py processing."""

from __future__ import annotations

import argparse
import builtins
import collections.abc
from collections.abc import Callable
from collections.abc import Generator
from collections.abc import Iterable
from collections.abc import Iterator
from collections.abc import Mapping
from collections.abc import MutableMapping
from collections.abc import Sequence
import contextlib
import copy
import dataclasses
import enum
from functools import lru_cache
import glob
import importlib
import importlib.metadata
import inspect
import os
import pathlib
import re
import shlex
import sys
from textwrap import dedent
import types
from types import FunctionType
from typing import Any
from typing import cast
from typing import Final
from typing import final
from typing import IO
from typing import TextIO
from typing import TYPE_CHECKING
import warnings

from pluggy import HookimplMarker
from pluggy import HookimplOpts
from pluggy import HookspecMarker
from pluggy import HookspecOpts
from pluggy import PluginManager

from .exceptions import PrintHelp as PrintHelp
from .exceptions import UsageError as UsageError
from .findpaths import ConfigDict
from .findpaths import ConfigValue
from .findpaths import determine_setup
from .findpaths import parse_override_ini
from _pytest import __version__
import _pytest._code
from _pytest._code import ExceptionInfo
from _pytest._code import filter_traceback
from _pytest._code.code import TracebackStyle
from _pytest._io import TerminalWriter
from _pytest.compat import assert_never
from _pytest.compat import deprecated
from _pytest.compat import NOTSET
from _pytest.config.argparsing import Argument
from _pytest.config.argparsing import FILE_OR_DIR
from _pytest.config.argparsing import Parser
import _pytest.deprecated
import _pytest.hookspec
from _pytest.outcomes import fail
from _pytest.outcomes import Skipped
from _pytest.pathlib import absolutepath
from _pytest.pathlib import bestrelpath
from _pytest.pathlib import import_path
from _pytest.pathlib import ImportMode
from _pytest.pathlib import resolve_package_path
from _pytest.pathlib import safe_exists
from _pytest.stash import Stash
from _pytest.warning_types import PytestConfigWarning
from _pytest.warning_types import warn_explicit_for


if TYPE_CHECKING:
    from _pytest.assertion.rewrite import AssertionRewritingHook
    from _pytest.cacheprovider import Cache
    from _pytest.terminal import TerminalReporter

_PluggyPlugin = object
"""A type to represent plugin objects.

Plugins can be any namespace, so we can't narrow it down much, but we use an
alias to make the intent clear.

Ideally this type would be provided by pluggy itself.
"""


hookimpl = HookimplMarker("pytest")
hookspec = HookspecMarker("pytest")


@final
class ExitCode(enum.IntEnum):
    """Encodes the valid exit codes by pytest.

    Currently users and plugins may supply other exit codes as well.

    .. versionadded:: 5.0
    """

    #: Tests passed.
    OK = 0
    #: Tests failed.
    TESTS_FAILED = 1
    #: pytest was interrupted.
    INTERRUPTED = 2
    #: An internal error got in the way.
    INTERNAL_ERROR = 3
    #: pytest was misused.
    USAGE_ERROR = 4
    #: pytest couldn't find tests.
    NO_TESTS_COLLECTED = 5
    #: All tests pass, but maximum number of warnings exceeded.
    MAX_WARNINGS_ERROR = 6

    __module__ = "pytest"


class ConftestImportFailure(Exception):
    def __init__(
        self,
        path: pathlib.Path,
        *,
        cause: Exception,
    ) -> None:
        self.path = path
        self.cause = cause

    def __str__(self) -> str:
        return f"{type(self.cause).__name__}: {self.cause} (from {self.path})"


def filter_traceback_for_conftest_import_failure(
    entry: _pytest._code.TracebackEntry,
) -> bool:
    """Filter tracebacks entries which point to pytest internals or importlib.

    Make a special case for importlib because we use it to import test modules and conftest files
    in _pytest.pathlib.import_path.
    """
    return filter_traceback(entry) and "importlib" not in str(entry.path).split(os.sep)


def print_conftest_import_error(e: ConftestImportFailure, file: TextIO) -> None:
    exc_info = ExceptionInfo.from_exception(e.cause)
    tw = TerminalWriter(file)
    tw.line(f"ImportError while loading conftest '{e.path}'.", red=True)
    exc_info.traceback = exc_info.traceback.filter(
        filter_traceback_for_conftest_import_failure
    )
    exc_repr = (
        exc_info.getrepr(style="short", chain=False)
        if exc_info.traceback
        else exc_info.exconly()
    )
    formatted_tb = str(exc_repr)
    for line in formatted_tb.splitlines():
        tw.line(line.rstrip(), red=True)


def print_usage_error(e: UsageError, file: TextIO) -> None:
    tw = TerminalWriter(file)
    for msg in e.args:
        tw.line(f"ERROR: {msg}\n", red=True)


def _get_prog_name(argv: Sequence[str]) -> str:
    """Determine the CLI program name from the argument vector.

    :param argv: The argument vector (typically ``sys.argv``).
    :returns: ``"python -m pytest"`` when invoked via ``python -m``,
              ``"pytest"`` otherwise.
    """
    argv0 = argv[0] if argv else ""
    if os.path.basename(argv0) == "__main__.py":
        return "python -m pytest"
    return "pytest"


def main(
    args: list[str] | os.PathLike[str] | None = None,
    plugins: Sequence[str | _PluggyPlugin] | None = None,
) -> int | ExitCode:
    """Perform an in-process test run.

    :param args:
        List of command line arguments. If `None` or not given, defaults to reading
        arguments directly from the process command line (:data:`sys.argv`).
    :param plugins: List of plugin objects to be auto-registered during initialization.

    :returns: An exit code.
    """
    return _main(args=args, plugins=plugins, prog="pytest.main()")


def _main(
    *,
    args: list[str] | os.PathLike[str] | None = None,
    plugins: Sequence[str | _PluggyPlugin] | None = None,
    prog: str,
) -> int | ExitCode:
    # Handle a single `--version`/`-V` argument early to avoid starting up the entire pytest infrastructure.
    new_args = sys.argv[1:] if args is None else args
    if (
        isinstance(new_args, Sequence)
        and (new_args.count("--version") + new_args.count("-V")) == 1
    ):
        sys.stdout.write(f"pytest {__version__}\n")
        return ExitCode.OK

    old_pytest_version = os.environ.get("PYTEST_VERSION")
    try:
        os.environ["PYTEST_VERSION"] = __version__
        try:
            config = _prepareconfig(new_args, plugins, prog=prog)
        except ConftestImportFailure as e:
            print_conftest_import_error(e, file=sys.stderr)
            return ExitCode.USAGE_ERROR

        try:
            ret: ExitCode | int = config.hook.pytest_cmdline_main(config=config)
            try:
                return ExitCode(ret)
            except ValueError:
                return ret
        finally:
            config._ensure_unconfigure()
    except UsageError as e:
        print_usage_error(e, file=sys.stderr)
        return ExitCode.USAGE_ERROR
    finally:
        if old_pytest_version is None:
            os.environ.pop("PYTEST_VERSION", None)
        else:
            os.environ["PYTEST_VERSION"] = old_pytest_version


def _console_main() -> int:
    """The CLI entry point of pytest (internal).

    This is the real implementation used by entry points and ``__main__.py``.
    """
    # https://docs.python.org/3/library/signal.html#note-on-sigpipe
    try:
        code = _main(prog=_get_prog_name(sys.argv))
        sys.stdout.flush()
        return code
    except BrokenPipeError:
        # Python flushes standard streams on exit; redirect remaining output
        # to devnull to avoid another BrokenPipeError at shutdown
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        return 1  # Python exits with error code 1 on EPIPE


def console_main() -> int:
    """The CLI entry point of pytest.

    .. deprecated:: 9.1
        This function is slated for removal in pytest 10.
        It is not meant for programmable use; use :func:`pytest.main` instead.
    """
    import warnings

    from _pytest.deprecated import CONSOLE_MAIN

    warnings.warn(CONSOLE_MAIN, stacklevel=2)
    return _console_main()


class cmdline:  # compatibility namespace
    main = staticmethod(main)


def filename_arg(path: str, optname: str) -> str:
    """Argparse type validator for filename arguments.

    :path: Path of filename.
    :optname: Name of the option.
    """
    if os.path.isdir(path):
        raise UsageError(f"{optname} must be a filename, given: {path}")
    return path


def directory_arg(path: str, optname: str) -> str:
    """Argparse type validator for directory arguments.

    :path: Path of directory.
    :optname: Name of the option.
    """
    if not os.path.isdir(path):
        raise UsageError(f"{optname} must be a directory, given: {path}")
    return path


# Plugins that cannot be disabled via "-p no:X" currently.
essential_plugins = (
    "mark",
    "main",
    "runner",
    "fixtures",
    "helpconfig",  # Provides -p.
)

default_plugins = (
    *essential_plugins,
    "python",
    "terminal",
    "debugging",
    "unittest",
    "capture",
    "skipping",
    "legacypath",
    "tmpdir",
    "monkeypatch",
    "recwarn",
    "pastebin",
    "assertion",
    "junitxml",
    "doctest",
    "cacheprovider",
    "setuponly",
    "setupplan",
    "stepwise",
    "unraisableexception",
    "threadexception",
    "warnings",
    "logging",
    "reports",
    "faulthandler",
    "subtests",
)

builtin_plugins = {
    *default_plugins,
    "pytester",
    "pytester_assertions",
    "terminalprogress",
}


def get_config(
    args: Iterable[str] | None = None,
    plugins: Sequence[str | _PluggyPlugin] | None = None,
    *,
    prog: str | None = None,
) -> Config:
    # Subsequent calls to main will create a fresh instance.
    pluginmanager = PytestPluginManager()
    invocation_params = Config.InvocationParams(
        args=args or (),
        plugins=plugins,
        dir=pathlib.Path.cwd(),
    )
    config = Config(pluginmanager, invocation_params=invocation_params, prog=prog)

    if invocation_params.args:
        # Handle any "-p no:plugin" args.
        pluginmanager.consider_preparse(invocation_params.args, exclude_only=True)

    for spec in default_plugins:
        pluginmanager.import_plugin(spec)

    return config


def get_plugin_manager() -> PytestPluginManager:
    """Obtain a new instance of the
    :py:class:`pytest.PytestPluginManager`, with default plugins
    already loaded.

    This function can be used by integration with other tools, like hooking
    into pytest to run tests into an IDE.
    """
    return get_config().pluginmanager


def _prepareconfig(
    args: list[str] | os.PathLike[str],
    plugins: Sequence[str | _PluggyPlugin] | None = None,
    *,
    prog: str | None = None,
) -> Config:
    if isinstance(args, os.PathLike):
        args = [os.fspath(args)]
    elif not isinstance(args, list):
        msg = (  # type:ignore[unreachable]
            "`args` parameter expected to be a list of strings, got: {!r} (type: {})"
        )
        raise TypeError(msg.format(args, type(args)))

    initial_config = get_config(args, plugins, prog=prog)
    pluginmanager = initial_config.pluginmanager
    try:
        if plugins:
            for plugin in plugins:
                if isinstance(plugin, str):
                    pluginmanager.consider_pluginarg(plugin)
                else:
                    pluginmanager.register(plugin)
        config: Config = pluginmanager.hook.pytest_cmdline_parse(
            pluginmanager=pluginmanager, args=args
        )
        return config
    except BaseException:
        initial_config._ensure_unconfigure()
        raise


def _get_directory(path: pathlib.Path) -> pathlib.Path:
    """Get the directory of a path - itself if already a directory."""
    if path.is_file():
        return path.parent
    else:
        return path


def _get_legacy_hook_marks(
    method: Any,
    hook_type: str,
    opt_names: tuple[str, ...],
) -> dict[str, bool]:
    if TYPE_CHECKING:
        # abuse typeguard from importlib to avoid massive method type union that's lacking an alias
        assert inspect.isroutine(method)
    known_marks: set[str] = {m.name for m in getattr(method, "pytestmark", [])}
    must_warn: list[str] = []
    opts: dict[str, bool] = {}
    for opt_name in opt_names:
        opt_attr = getattr(method, opt_name, AttributeError)
        if opt_attr is not AttributeError:
            must_warn.append(f"{opt_name}={opt_attr}")
            opts[opt_name] = True
        elif opt_name in known_marks:
            must_warn.append(f"{opt_name}=True")
            opts[opt_name] = True
        else:
            opts[opt_name] = False
    if must_warn:
        hook_opts = ", ".join(must_warn)
        message = _pytest.deprecated.HOOK_LEGACY_MARKING.format(
            type=hook_type,
            fullname=method.__qualname__,
            hook_opts=hook_opts,
        )
        warn_explicit_for(cast(FunctionType, method), message)
    return opts


@final
class PytestPluginManager(PluginManager):
    """A :py:class:`pluggy.PluginManager <pluggy.PluginManager>` with
    additional pytest-specific functionality:

    * Loading plugins from the command line, ``PYTEST_PLUGINS`` env variable and
      ``pytest_plugins`` global variables found in plugins being loaded.
    * ``conftest.py`` loading during start-up.
    """

    def __init__(self) -> None:
        from _pytest.assertion import DummyRewriteHook
        from _pytest.assertion import RewriteHook

        super().__init__("pytest")

        # -- State related to local conftest plugins.
        # All loaded conftest modules.
        self._conftest_plugins: set[types.ModuleType] = set()
        # All conftest modules applicable for a directory.
        # This includes the directory's own conftest modules as well
        # as those of its parent directories.
        self._dirpath2confmods: dict[pathlib.Path, list[types.ModuleType]] = {}
        # Cutoff directory above which conftests are no longer discovered.
        self._confcutdir: pathlib.Path | None = None
        # If set, conftest loading is skipped.
        self._noconftest = False

        # _getconftestmodules()'s call to _get_directory() causes a stat
        # storm when it's called potentially thousands of times in a test
        # session (#9478), often with the same path, so cache it.
        self._get_directory = lru_cache(256)(_get_directory)

        # plugins that were explicitly skipped with pytest.skip
        # list of (module name, skip reason)
        # previously we would issue a warning when a plugin was skipped, but
        # since we refactored warnings as first citizens of Config, they are
        # just stored here to be used later.
        self.skipped_plugins: list[tuple[str, str]] = []

        self.add_hookspecs(_pytest.hookspec)
        self.register(self)
        if os.environ.get("PYTEST_DEBUG"):
            err: IO[str] = sys.stderr
            encoding: str = getattr(err, "encoding", "utf8")
            try:
                err = open(
                    os.dup(err.fileno()),
                    mode=err.mode,
                    buffering=1,
                    encoding=encoding,
                )
            except Exception:
                pass
            self.trace.root.setwriter(err.write)
            self.enable_tracing()

        # Config._consider_importhook will set a real object if required.
        self.rewrite_hook: RewriteHook = DummyRewriteHook()
        # Used to know when we are importing conftests after the pytest_configure stage.
        self._configured = False

    def parse_hookimpl_opts(
        self, plugin: _PluggyPlugin, name: str
    ) -> HookimplOpts | None:
        """:meta private:"""
        # pytest hooks are always prefixed with "pytest_",
        # so we avoid accessing possibly non-readable attributes
        # (see issue #1073).
        if not name.startswith("pytest_"):
            return None
        # Ignore names which cannot be hooks.
        if name == "pytest_plugins":
