from __future__ import annotations

import collections.abc as cabc
import enum
import errno
import inspect
import os
import sys
import typing as t
from abc import ABC
from abc import abstractmethod
from collections import abc
from collections import Counter
from contextlib import AbstractContextManager
from contextlib import contextmanager
from contextlib import ExitStack
from functools import update_wrapper
from gettext import gettext as _
from gettext import ngettext
from itertools import repeat
from types import TracebackType

from . import types
from ._utils import FLAG_NEEDS_VALUE
from ._utils import UNSET
from .exceptions import Abort
from .exceptions import BadParameter
from .exceptions import ClickException
from .exceptions import Exit
from .exceptions import MissingParameter
from .exceptions import NoArgsIsHelpError
from .exceptions import NoSuchCommand
from .exceptions import UsageError
from .formatting import HelpFormatter
from .formatting import join_options
from .globals import pop_context
from .globals import push_context
from .parser import _OptionParser
from .parser import _split_opt
from .termui import confirm
from .termui import prompt
from .termui import style
from .utils import _detect_program_name
from .utils import _expand_args
from .utils import _make_default_short_help
from .utils import _PacifyFlushWrapper
from .utils import echo
from .utils import make_str

if t.TYPE_CHECKING:
    from typing_extensions import Self

    from .shell_completion import CompletionItem

F = t.TypeVar("F", bound="t.Callable[..., t.Any]")
V = t.TypeVar("V")

# Reserved storage name of the automatic help option. No user parameter is
# expected to claim it.
_HELP_OPTION_STORAGE_NAME = "_click_default_help"


def _complete_visible_commands(
    ctx: Context, incomplete: str
) -> cabc.Iterator[tuple[str, Command]]:
    """List all the subcommands of a group that start with the
    incomplete value and aren't hidden.

    :param ctx: Invocation context for the group.
    :param incomplete: Value being completed. May be empty.
    """
    multi = t.cast(Group, ctx.command)

    for name in multi.list_commands(ctx):
        if name.startswith(incomplete):
            command = multi.get_command(ctx, name)

            if command is not None and not command.hidden:
                yield name, command


def _check_nested_chain(
    base_command: Group, cmd_name: str, cmd: Command, register: bool = False
) -> None:
    if not base_command.chain or not isinstance(cmd, Group):
        return

    if register:
        message = (
            f"It is not possible to add the group {cmd_name!r} to another"
            f" group {base_command.name!r} that is in chain mode."
        )
    else:
        message = (
            f"Found the group {cmd_name!r} as subcommand to another group "
            f" {base_command.name!r} that is in chain mode. This is not supported."
        )

    raise RuntimeError(message)


def _format_deprecated_label(deprecated: bool | str) -> str:
    """Return the parenthesized deprecation label shown in help text."""
    label = _("deprecated").upper()
    if isinstance(deprecated, str):
        return f"({label}: {deprecated})"
    return f"({label})"


def _format_deprecated_suffix(deprecated: bool | str) -> str:
    """Return the trailing reason for a ``DeprecationWarning`` message,
    prefixed with a space, or an empty string when no reason was given.
    """
    if isinstance(deprecated, str):
        return f" {deprecated}"
    return ""


def batch(iterable: cabc.Iterable[V], batch_size: int) -> list[tuple[V, ...]]:
    return list(zip(*repeat(iter(iterable), batch_size), strict=False))


@contextmanager
def augment_usage_errors(
    ctx: Context, param: Parameter | None = None
) -> cabc.Generator[None]:
    """Context manager that attaches extra information to exceptions."""
    try:
        yield
    except BadParameter as e:
        if e.ctx is None:
            e.ctx = ctx
        if param is not None and e.param is None:
            e.param = param
        raise
    except UsageError as e:
        if e.ctx is None:
            e.ctx = ctx
        raise


def iter_params_for_processing(
    invocation_order: cabc.Sequence[Parameter],
    declaration_order: cabc.Sequence[Parameter],
) -> list[Parameter]:
    """Returns all declared parameters in the order they should be processed.

    The declared parameters are re-shuffled depending on the order in which
    they were invoked, as well as the eagerness of each parameters.

    The invocation order takes precedence over the declaration order. I.e. the
    order in which the user provided them to the CLI is respected.

    This behavior and its effect on callback evaluation is detailed at:
    https://click.palletsprojects.com/en/stable/advanced/#callback-evaluation-order
    """

    def sort_key(item: Parameter) -> tuple[bool, float]:
        try:
            idx: float = invocation_order.index(item)
        except ValueError:
            idx = float("inf")

        return not item.is_eager, idx

    return sorted(declaration_order, key=sort_key)


class ParameterSource(enum.IntEnum):
    """This is an :class:`~enum.IntEnum` that indicates the source of a
    parameter's value.

    Use :meth:`click.Context.get_parameter_source` to get the
    source for a parameter by name.

    Members are ordered from most explicit to least explicit source.
    This allows comparison to check if a value was explicitly provided:

    .. code-block:: python

        source = ctx.get_parameter_source("port")
        if source < click.ParameterSource.DEFAULT_MAP:
            ...  # value was explicitly set

    .. versionchanged:: 8.3.3
        Use :class:`~enum.IntEnum` and reorder members from most to
        least explicit. Supports comparison operators.

    .. versionchanged:: 8.0
        Use :class:`~enum.Enum` and drop the ``validate`` method.

    .. versionchanged:: 8.0
        Added the ``PROMPT`` value.
    """

    PROMPT = enum.auto()
    """Used a prompt to confirm a default or provide a value."""
    COMMANDLINE = enum.auto()
    """The value was provided by the command line args."""
    ENVIRONMENT = enum.auto()
    """The value was provided with an environment variable."""
    DEFAULT_MAP = enum.auto()
    """Used a default provided by :attr:`Context.default_map`."""
    DEFAULT = enum.auto()
    """Used the default specified by the parameter."""


class Context:
    """The context is a special internal object that holds state relevant
    for the script execution at every single level.  It's normally invisible
    to commands unless they opt-in to getting access to it.

    The context is useful as it can pass internal objects around and can
    control special execution features such as reading data from
    environment variables.

    A context can be used as context manager in which case it will call
    :meth:`close` on teardown.

    :param command: the command class for this context.
    :param parent: the parent context.
    :param info_name: the info name for this invocation.  Generally this
                      is the most descriptive name for the script or
                      command.  For the toplevel script it is usually
                      the name of the script, for commands below that it's
                      the name of the script.
    :param obj: an arbitrary object of user data.
    :param auto_envvar_prefix: the prefix to use for automatic environment
                               variables.  If this is `None` then reading
                               from environment variables is disabled.  This
                               does not affect manually set environment
                               variables which are always read.
    :param default_map: a dictionary (like object) with default values
                        for parameters.
    :param terminal_width: the width of the terminal.  The default is
                           inherit from parent context.  If no context
                           defines the terminal width then auto
                           detection will be applied.
    :param max_content_width: the maximum width for content rendered by
                              Click (this currently only affects help
                              pages).  This defaults to 80 characters if
                              not overridden.  In other words: even if the
                              terminal is larger than that, Click will not
                              format things wider than 80 characters by
                              default.  In addition to that, formatters might
                              add some safety mapping on the right.
    :param resilient_parsing: if this flag is enabled then Click will
                              parse without any interactivity or callback
                              invocation.  Default values will also be
                              ignored.  This is useful for implementing
                              things such as completion support.
    :param allow_extra_args: if this is set to `True` then extra arguments
                             at the end will not raise an error and will be
                             kept on the context.  The default is to inherit
                             from the command.
    :param allow_interspersed_args: if this is set to `False` then options
                                    and arguments cannot be mixed.  The
                                    default is to inherit from the command.
    :param ignore_unknown_options: instructs click to ignore options it does
                                   not know and keeps them for later
                                   processing.
    :param help_option_names: optionally a list of strings that define how
                              the default help parameter is named.  The
                              default is ``['--help']``.
    :param token_normalize_func: an optional function that is used to
                                 normalize tokens (options, choices,
                                 etc.).  This for instance can be used to
                                 implement case insensitive behavior.
    :param color: controls if the terminal supports ANSI colors or not.  The
                  default is autodetection.  This is only needed if ANSI
                  codes are used in texts that Click prints which is by
                  default not the case.  This for instance would affect
                  help output.
    :param show_default: Show the default value for commands. If this
        value is not set, it defaults to the value from the parent
        context. ``Command.show_default`` overrides this default for the
        specific command.

    .. versionchanged:: 8.2
        The ``protected_args`` attribute is deprecated and will be removed in
        Click 9.0. ``args`` will contain remaining unparsed tokens.

    .. versionchanged:: 8.1
        The ``show_default`` parameter is overridden by
        ``Command.show_default``, instead of the other way around.

    .. versionchanged:: 8.0
        The ``show_default`` parameter defaults to the value from the
        parent context.

    .. versionchanged:: 7.1
       Added the ``show_default`` parameter.

    .. versionchanged:: 4.0
        Added the ``color``, ``ignore_unknown_options``, and
        ``max_content_width`` parameters.

    .. versionchanged:: 3.0
        Added the ``allow_extra_args`` and ``allow_interspersed_args``
        parameters.

    .. versionchanged:: 2.0
        Added the ``resilient_parsing``, ``help_option_names``, and
        ``token_normalize_func`` parameters.
    """

    #: The formatter class to create with :meth:`make_formatter`.
    #:
    #: .. versionadded:: 8.0
    formatter_class: type[HelpFormatter] = HelpFormatter

    parent: Context | None
    command: Command
    info_name: str | None
    params: dict[str, t.Any]
    args: list[str]
    _protected_args: list[str]
    _opt_prefixes: set[str]
    obj: t.Any
    _meta: dict[str, t.Any]
    default_map: cabc.MutableMapping[str, t.Any] | None
    invoked_subcommand: str | None
    terminal_width: int | None
    max_content_width: int | None
    allow_extra_args: bool
    allow_interspersed_args: bool
    ignore_unknown_options: bool
    help_option_names: list[str]
    token_normalize_func: t.Callable[[str], str] | None
    resilient_parsing: bool
    auto_envvar_prefix: str | None
    color: bool | None
    show_default: bool | None
    _close_callbacks: list[t.Callable[[], t.Any]]
    _depth: int
    _parameter_source: dict[str, ParameterSource]
    _param_default_explicit: dict[str, bool]
    _exit_stack: ExitStack

    def __init__(
        self,
        command: Command,
        parent: Context | None = None,
        info_name: str | None = None,
        obj: t.Any | None = None,
        auto_envvar_prefix: str | None = None,
        default_map: cabc.MutableMapping[str, t.Any] | None = None,
        terminal_width: int | None = None,
        max_content_width: int | None = None,
        resilient_parsing: bool = False,
        allow_extra_args: bool | None = None,
        allow_interspersed_args: bool | None = None,
        ignore_unknown_options: bool | None = None,
        help_option_names: list[str] | None = None,
        token_normalize_func: t.Callable[[str], str] | None = None,
        color: bool | None = None,
        show_default: bool | None = None,
    ) -> None:
        #: the parent context or `None` if none exists.
        self.parent = parent
        #: the :class:`Command` for this context.
        self.command = command
        #: the descriptive information name
        self.info_name = info_name
        #: Map of parameter names to their parsed values. Parameters
        #: with ``expose_value=False`` are not stored.
        self.params = {}
        #: the leftover arguments.
        self.args = []
        #: protected arguments.  These are arguments that are prepended
        #: to `args` when certain parsing scenarios are encountered but
        #: must be never propagated to another arguments.  This is used
        #: to implement nested parsing.
        self._protected_args = []
        #: the collected prefixes of the command's options.
        self._opt_prefixes = set(parent._opt_prefixes) if parent else set()

        if obj is None and parent is not None:
            obj = parent.obj

        #: the user object stored.
        self.obj = obj
        self._meta = getattr(parent, "meta", {})

        #: A dictionary (-like object) with defaults for parameters.
        if (
            default_map is None
            and info_name is not None
            and parent is not None
            and parent.default_map is not None
        ):
            default_map = parent.default_map.get(info_name)

        self.default_map = default_map

        #: This flag indicates if a subcommand is going to be executed. A
        #: group callback can use this information to figure out if it's
        #: being executed directly or because the execution flow passes
        #: onwards to a subcommand. By default it's None, but it can be
        #: the name of the subcommand to execute.
        #:
        #: If chaining is enabled this will be set to ``'*'`` in case
        #: any commands are executed.  It is however not possible to
        #: figure out which ones.  If you require this knowledge you
        #: should use a :func:`result_callback`.
        self.invoked_subcommand = None

        if terminal_width is None and parent is not None:
            terminal_width = parent.terminal_width

        #: The width of the terminal (None is autodetection).
        self.terminal_width = terminal_width

        if max_content_width is None and parent is not None:
            max_content_width = parent.max_content_width

        #: The maximum width of formatted content (None implies a sensible
        #: default which is 80 for most things).
        self.max_content_width = max_content_width

        if allow_extra_args is None:
            allow_extra_args = command.allow_extra_args

        #: Indicates if the context allows extra args or if it should
        #: fail on parsing.
        #:
        #: .. versionadded:: 3.0
