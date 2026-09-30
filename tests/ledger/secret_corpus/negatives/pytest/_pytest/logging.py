# mypy: allow-untyped-defs
"""Access and control log capturing."""

from __future__ import annotations

from collections.abc import Generator
from collections.abc import Mapping
from collections.abc import Set as AbstractSet
from contextlib import contextmanager
from contextlib import nullcontext
from datetime import datetime
from datetime import timedelta
from datetime import timezone
import io
from io import StringIO
import logging
from logging import LogRecord
import os
from pathlib import Path
import re
from types import TracebackType
from typing import final
from typing import Generic
from typing import Literal
from typing import TYPE_CHECKING
from typing import TypeVar

from _pytest import nodes
from _pytest._io import TerminalWriter
from _pytest.capture import CaptureManager
from _pytest.config import _strtobool
from _pytest.config import Config
from _pytest.config import create_terminal_writer
from _pytest.config import hookimpl
from _pytest.config import UsageError
from _pytest.config.argparsing import Parser
from _pytest.deprecated import check_ispytest
from _pytest.fixtures import fixture
from _pytest.fixtures import FixtureRequest
from _pytest.main import Session
from _pytest.stash import StashKey
from _pytest.terminal import TerminalReporter


if TYPE_CHECKING:
    logging_StreamHandler = logging.StreamHandler[StringIO]
else:
    logging_StreamHandler = logging.StreamHandler

DEFAULT_LOG_FORMAT = "%(levelname)-8s %(name)s:%(filename)s:%(lineno)d %(message)s"
DEFAULT_LOG_DATE_FORMAT = "%H:%M:%S"
_ANSI_ESCAPE_SEQ = re.compile(r"\x1b\[[\d;]+m")
caplog_handler_key = StashKey["LogCaptureHandler"]()
caplog_records_key = StashKey[dict[str, list[logging.LogRecord]]]()


def _remove_ansi_escape_sequences(text: str) -> str:
    return _ANSI_ESCAPE_SEQ.sub("", text)


class DatetimeFormatter(logging.Formatter):
    """A logging formatter which formats record with
    :func:`datetime.datetime.strftime` formatter instead of
    :func:`time.strftime` in case of microseconds in format string.
    """

    def formatTime(self, record: LogRecord, datefmt: str | None = None) -> str:
        if datefmt and "%f" in datefmt:
            ct = self.converter(record.created)
            tz = timezone(timedelta(seconds=ct.tm_gmtoff), ct.tm_zone)
            # Construct `datetime.datetime` object from `struct_time`
            # and msecs information from `record`
            # Using int() instead of round() to avoid it exceeding 1_000_000 and causing a ValueError (#11861).
            dt = datetime(*ct[0:6], microsecond=int(record.msecs * 1000), tzinfo=tz)
            return dt.strftime(datefmt)
        # Use `logging.Formatter` for non-microsecond formats
        return super().formatTime(record, datefmt)


class ColoredLevelFormatter(DatetimeFormatter):
    """A logging formatter which colorizes the %(levelname)..s part of the
    log format passed to __init__."""

    LOGLEVEL_COLOROPTS: Mapping[int, AbstractSet[str]] = {
        logging.CRITICAL: {"red"},
        logging.ERROR: {"red", "bold"},
        logging.WARNING: {"yellow"},
        logging.WARN: {"yellow"},
        logging.INFO: {"green"},
        logging.DEBUG: {"purple"},
        logging.NOTSET: set(),
    }
    LEVELNAME_FMT_REGEX = re.compile(r"%\(levelname\)([+-.]?\d*(?:\.\d+)?s)")

    def __init__(self, terminalwriter: TerminalWriter, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._terminalwriter = terminalwriter
        self._original_fmt = self._style._fmt
        self._level_to_fmt_mapping: dict[int, str] = {}

        for level, color_opts in self.LOGLEVEL_COLOROPTS.items():
            self.add_color_level(level, *color_opts)

    def add_color_level(self, level: int, *color_opts: str) -> None:
        """Add or update color opts for a log level.

        :param level:
            Log level to apply a style to, e.g. ``logging.INFO``.
        :param color_opts:
            ANSI escape sequence color options. Capitalized colors indicates
            background color, i.e. ``'green', 'Yellow', 'bold'`` will give bold
            green text on yellow background.

        .. warning::
            This is an experimental API.
        """
        assert self._fmt is not None
        levelname_fmt_match = self.LEVELNAME_FMT_REGEX.search(self._fmt)
        if not levelname_fmt_match:
            return
        levelname_fmt = levelname_fmt_match.group()

        formatted_levelname = levelname_fmt % {"levelname": logging.getLevelName(level)}

        # add ANSI escape sequences around the formatted levelname
        color_kwargs = {name: True for name in color_opts}
        colorized_formatted_levelname = self._terminalwriter.markup(
            formatted_levelname, **color_kwargs
        )
        self._level_to_fmt_mapping[level] = self.LEVELNAME_FMT_REGEX.sub(
            colorized_formatted_levelname, self._fmt
        )

    def format(self, record: logging.LogRecord) -> str:
        fmt = self._level_to_fmt_mapping.get(record.levelno, self._original_fmt)
        self._style._fmt = fmt
        return super().format(record)


class PercentStyleMultiline(logging.PercentStyle):
    """A logging style with special support for multiline messages.

    If the message of a record consists of multiple lines, this style
    formats the message as if each line were logged separately.
    """

    def __init__(self, fmt: str, auto_indent: int | str | bool | None) -> None:
        super().__init__(fmt)
        self._auto_indent = self._get_auto_indent(auto_indent)

    @staticmethod
    def _get_auto_indent(auto_indent_option: int | str | bool | None) -> int:
        """Determine the current auto indentation setting.

        Specify auto indent behavior (on/off/fixed) by passing in
        extra={"auto_indent": [value]} to the call to logging.log() or
        using a --log-auto-indent [value] command line or the
        log_auto_indent [value] config option.

        Default behavior is auto-indent off.

        Using the string "True" or "on" or the boolean True as the value
        turns auto indent on, using the string "False" or "off" or the
        boolean False or the int 0 turns it off, and specifying a
        positive integer fixes the indentation position to the value
        specified.

        Any other values for the option are invalid, and will silently be
        converted to the default.

        :param None|bool|int|str auto_indent_option:
            User specified option for indentation from command line, config
            or extra kwarg. Accepts int, bool or str. str option accepts the
            same range of values as boolean config options, as well as
            positive integers represented in str form.

        :returns:
            Indentation value, which can be
            -1 (automatically determine indentation) or
            0 (auto-indent turned off) or
            >0 (explicitly set indentation position).
        """
        match auto_indent_option:
            case None | False:
                return 0
            case True:
                return -1
            case int():
                return auto_indent_option
            case str():
                try:
                    return int(auto_indent_option)
                except ValueError:
                    pass
                try:
                    if _strtobool(auto_indent_option):
                        return -1
                except ValueError:
                    return 0
                return 0

    def format(self, record: logging.LogRecord) -> str:
        if "\n" in record.message:
            if hasattr(record, "auto_indent"):
                # Passed in from the "extra={}" kwarg on the call to logging.log().
                auto_indent = self._get_auto_indent(record.auto_indent)
            else:
                auto_indent = self._auto_indent

            if auto_indent:
                lines = record.message.splitlines()
                formatted = self._fmt % {**record.__dict__, "message": lines[0]}

                if auto_indent < 0:
                    indentation = _remove_ansi_escape_sequences(formatted).find(
                        lines[0]
                    )
                else:
                    # Optimizes logging by allowing a fixed indentation.
                    indentation = auto_indent
                lines[0] = formatted
                return ("\n" + " " * indentation).join(lines)
        return self._fmt % record.__dict__


def get_option_ini(config: Config, *names: str):
    for name in names:
        ret = config.getoption(name)  # 'default' arg won't work as expected
        if ret is None:
            ret = config.getini(name)
        if ret:
            return ret


def pytest_addoption(parser: Parser) -> None:
    """Add options to control log capturing."""
    group = parser.getgroup("logging")

    def add_option_ini(option, dest, default=None, type=None, **kwargs):
        parser.addini(
            dest, default=default, type=type, help="Default value for " + option
        )
        group.addoption(option, dest=dest, **kwargs)

    add_option_ini(
        "--log-level",
        dest="log_level",
        default=None,
        metavar="LEVEL",
        help=(
            "Level of messages to catch/display."
            " Not set by default, so it depends on the root/parent log handler's"
            ' effective level, where it is "WARNING" by default.'
        ),
    )
    add_option_ini(
        "--log-format",
        dest="log_format",
        default=DEFAULT_LOG_FORMAT,
        help="Log format used by the logging module",
    )
    add_option_ini(
        "--log-date-format",
        dest="log_date_format",
        default=DEFAULT_LOG_DATE_FORMAT,
        help="Log date format used by the logging module",
    )
    parser.addini(
        "log_cli",
        default=False,
        type="bool",
        help='Enable log display during test run (also known as "live logging")',
    )
    add_option_ini(
        "--log-cli-level", dest="log_cli_level", default=None, help="CLI logging level"
    )
    add_option_ini(
        "--log-cli-format",
        dest="log_cli_format",
        default=None,
        help="Log format used by the logging module",
    )
    add_option_ini(
        "--log-cli-date-format",
        dest="log_cli_date_format",
        default=None,
        help="Log date format used by the logging module",
    )
    add_option_ini(
        "--log-file",
        dest="log_file",
        default=None,
        help="Path to a file when logging will be written to",
    )
    add_option_ini(
        "--log-file-mode",
        dest="log_file_mode",
        default="w",
        choices=["w", "a"],
        help="Log file open mode",
    )
    add_option_ini(
        "--log-file-level",
        dest="log_file_level",
        default=None,
        help="Log file logging level",
    )
    add_option_ini(
        "--log-file-format",
        dest="log_file_format",
        default=None,
        help="Log format used by the logging module",
    )
    add_option_ini(
        "--log-file-date-format",
        dest="log_file_date_format",
        default=None,
        help="Log date format used by the logging module",
    )
    add_option_ini(
        "--log-auto-indent",
        dest="log_auto_indent",
        default=None,
        help="Auto-indent multiline messages passed to the logging module. Accepts true|on, false|off or an integer.",
    )
    group.addoption(
        "--log-disable",
        action="append",
        default=[],
        dest="logger_disable",
        help="Disable a logger by name. Can be passed multiple times.",
    )


_HandlerType = TypeVar("_HandlerType", bound=logging.Handler)


# Not using @contextmanager for performance reasons.
class catching_logs(Generic[_HandlerType]):
    """Context manager that prepares the whole logging machinery properly."""

    __slots__ = ("attached_loggers", "handler", "level", "orig_level")

    def __init__(self, handler: _HandlerType, level: int | None = None) -> None:
        self.handler = handler
        self.level = level
        self.attached_loggers: list[logging.Logger] = []

    def __enter__(self) -> _HandlerType:
        root_logger = logging.getLogger()
        if self.level is not None:
            self.handler.setLevel(self.level)
        # Attach to root logger.
        root_logger.addHandler(self.handler)
        self.attached_loggers.append(root_logger)
        # Attach to all non-propagating loggers (won't reach root).
        # Note that will miss loggers that *become* non-propagating
        # after the `__enter__`. Not worth the trouble for now.
        for logger in root_logger.manager.loggerDict.values():
            if (
                isinstance(logger, logging.Logger)
                and not logger.propagate
                and logger is not root_logger
            ):
                logger.addHandler(self.handler)
                self.attached_loggers.append(logger)
        if self.level is not None:
            # Non-propagating loggers still inherit the level (unless a logger
            # explicitly set level), so only do this on the root logger.
            self.orig_level = root_logger.level
            root_logger.setLevel(min(self.orig_level, self.level))
        return self.handler

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        root_logger = logging.getLogger()
        if self.level is not None:
            root_logger.setLevel(self.orig_level)
        for logger in self.attached_loggers:
            logger.removeHandler(self.handler)
        self.attached_loggers.clear()


class LogCaptureHandler(logging_StreamHandler):
    """A logging handler that stores log records and the log text."""

    def __init__(self) -> None:
        """Create a new log handler."""
        super().__init__(StringIO())
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        """Keep the log records in a list in addition to the log text."""
        self.records.append(record)
        super().emit(record)

    def reset(self) -> None:
        self.records = []
        self.stream = StringIO()

    def clear(self) -> None:
        self.records.clear()
        self.stream = StringIO()

    def handleError(self, record: logging.LogRecord) -> None:
        if logging.raiseExceptions:
            # Fail the test if the log message is bad (emit failed).
            # The default behavior of logging is to print "Logging error"
            # to stderr with the call stack and some extra details.
            # pytest wants to make such mistakes visible during testing.
            raise  # noqa: PLE0704


@final
class LogCaptureFixture:
    """Provides access and control of log capturing."""

    def __init__(self, item: nodes.Node, *, _ispytest: bool = False) -> None:
        check_ispytest(_ispytest)
        self._item = item
        self._initial_handler_level: int | None = None
        # Dict of log name -> log level.
        self._initial_logger_levels: dict[str | None, int] = {}
        self._initial_disabled_logging_level: int | None = None

    def _finalize(self) -> None:
        """Finalize the fixture.

        This restores the log levels and the disabled logging levels changed by :meth:`set_level`.
        """
        # Restore log levels.
        if self._initial_handler_level is not None:
            self.handler.setLevel(self._initial_handler_level)
        for logger_name, level in self._initial_logger_levels.items():
            logger = logging.getLogger(logger_name)
            logger.setLevel(level)
        # Disable logging at the original disabled logging level.
        if self._initial_disabled_logging_level is not None:
            logging.disable(self._initial_disabled_logging_level)
            self._initial_disabled_logging_level = None

    @property
    def handler(self) -> LogCaptureHandler:
        """Get the logging handler used by the fixture."""
        return self._item.stash[caplog_handler_key]

    def get_records(
        self, when: Literal["setup", "call", "teardown"]
    ) -> list[logging.LogRecord]:
