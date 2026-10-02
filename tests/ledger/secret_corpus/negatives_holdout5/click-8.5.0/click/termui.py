from __future__ import annotations

import collections.abc as cabc
import inspect
import io
import itertools
import os
import re
import sys
import typing as t
from contextlib import AbstractContextManager
from contextlib import redirect_stdout
from gettext import gettext as _

from . import _compat
from ._compat import isatty
from ._compat import strip_ansi
from .exceptions import Abort
from .exceptions import UsageError
from .globals import resolve_color_default
from .types import Choice
from .types import convert_type
from .types import ParamType
from .utils import _LazyFile
from .utils import echo

if t.TYPE_CHECKING:
    from ._termui_impl import ProgressBar

V = t.TypeVar("V")

# The prompt functions to use.  The doc tools currently override these
# functions to customize how they work.
visible_prompt_func: t.Callable[[str], str] = input

_ansi_colors = {
    "black": 30,
    "red": 31,
    "green": 32,
    "yellow": 33,
    "blue": 34,
    "magenta": 35,
    "cyan": 36,
    "white": 37,
    "reset": 39,
    "bright_black": 90,
    "bright_red": 91,
    "bright_green": 92,
    "bright_yellow": 93,
    "bright_blue": 94,
    "bright_magenta": 95,
    "bright_cyan": 96,
    "bright_white": 97,
}
_ansi_reset_all = "\033[0m"


_HIDDEN_INPUT_MASK = "'***'"


def _mask_hidden_input(message: str, value: str) -> str:
    """Replace occurrences of ``value`` in ``message`` with a fixed mask.

    Both ``repr(value)`` (the form built-in :class:`ParamType` errors use
    via ``{value!r}``) and the raw value are masked. The raw-value pass
    uses word-boundary lookarounds so a substring like ``"1"`` does not
    match inside ``"10"``, and ``"ent"`` does not match inside
    ``"Authentication"``. The empty string is skipped to avoid matching
    at every boundary.
    """
    message = message.replace(repr(value), _HIDDEN_INPUT_MASK)
    if value:
        message = re.sub(
            rf"(?<!\w){re.escape(value)}(?!\w)", _HIDDEN_INPUT_MASK, message
        )
    return message


def hidden_prompt_func(prompt: str) -> str:
    import getpass

    return getpass.getpass(prompt)


def _readline_prompt(func: t.Callable[[str], str], text: str, err: bool) -> str:
    """Call a prompt function, passing the full prompt so readline can
    handle line editing and cursor positioning correctly.

    The prompt is handed to *func* (such as :func:`input`) rather than
    written through :func:`echo`, so it has to strip ANSI color and style
    codes itself when the destination stream does not support them. Without
    this the prompt would keep codes that :func:`echo` removes from the
    rest of the output.
    """
    stream = sys.stderr if err else sys.stdout

    # Look up ``should_strip_ansi`` on the module so that ``CliRunner``,
    # which patches it there during test isolation, is honored.
    if _compat.should_strip_ansi(stream, resolve_color_default()):
        text = strip_ansi(text)

    if err:
        with redirect_stdout(sys.stderr):
            return func(text)
    return func(text)


def _build_prompt(
    text: str,
    suffix: str,
    show_default: bool | str = False,
    default: object | None = None,
    show_choices: bool = True,
    type: object | None = None,
) -> str:
    prompt = text
    if type is not None and show_choices and isinstance(type, Choice):
        prompt += f" ({', '.join(map(str, type.choices))})"
    default_preview = ""
    if show_default:
        if isinstance(show_default, str):
            default_preview = f" [({show_default})]"
        elif default is not None:
            default_preview = f" [{_format_default(default)}]"
    return f"{prompt}{default_preview}{suffix}"


def _format_default(default: V) -> V | str:
    if isinstance(default, (io.IOBase, _LazyFile)):
        name = getattr(default, "name", None)

        if name is not None:
            return str(name)

    return default


@t.overload
def prompt(
    text: str,
    default: str | None = None,
    hide_input: bool = False,
    confirmation_prompt: bool | str = False,
    type: None = None,
    value_proc: None = None,
    prompt_suffix: str = ": ",
    show_default: bool | str = True,
    err: bool = False,
    show_choices: bool = True,
) -> str: ...


@t.overload
def prompt(
    text: str,
    default: V | str | None = None,
    hide_input: bool = False,
    confirmation_prompt: bool | str = False,
    type: ParamType[V, str] | type[V] | None = None,
    value_proc: t.Callable[[str], V] | None = None,
    prompt_suffix: str = ": ",
    show_default: bool | str = True,
    err: bool = False,
    show_choices: bool = True,
) -> V: ...


def prompt(
    text: str,
    default: V | str | None = None,
    hide_input: bool = False,
    confirmation_prompt: bool | str = False,
    type: ParamType[V, str] | type[V] | None = None,
    value_proc: t.Callable[[str], V] | None = None,
    prompt_suffix: str = ": ",
    show_default: bool | str = True,
    err: bool = False,
    show_choices: bool = True,
) -> V:
    """Prompts a user for input.  This is a convenience function that can
    be used to prompt a user for input later.

    If the user aborts the input by sending an interrupt signal, this
    function will catch it and raise a :exc:`Abort` exception.

    :param text: the text to show for the prompt.
    :param default: the default value to use if no input happens.  If this
                    is not given it will prompt until it's aborted.
    :param hide_input: if this is set to true then the input value will
                       be hidden.
    :param confirmation_prompt: Prompt a second time to confirm the
        value. Can be set to a string instead of ``True`` to customize
        the message.
    :param type: the type to use to check the value against.
    :param value_proc: if this parameter is provided it's a function that
                       is invoked instead of the type conversion to
                       convert a value.
    :param prompt_suffix: a suffix that should be added to the prompt.
    :param show_default: shows or hides the default value in the prompt.
                         If this value is a string, it shows that string
                         in parentheses instead of the actual value.
    :param err: if set to true the file defaults to ``stderr`` instead of
                ``stdout``, the same as with echo.
    :param show_choices: Show or hide choices if the passed type is a Choice.
                         For example if type is a Choice of either day or week,
                         show_choices is true and text is "Group by" then the
                         prompt will be "Group by (day, week): ".

    .. versionchanged:: 8.5.0
        Generically typed: the return type is narrowed by ``type``,
        ``value_proc``, or ``default`` instead of being ``Any``. Runtime
        behavior is unchanged.

    .. versionchanged:: 8.3.3
        ``show_default`` can be a string to show a custom value instead
        of the actual default, matching the help text behavior.

    .. versionchanged:: 8.3.1
        A space is no longer appended to the prompt.

    .. versionadded:: 8.0
        ``confirmation_prompt`` can be a custom string.

    .. versionadded:: 7.0
        Added the ``show_choices`` parameter.

    .. versionadded:: 6.0
        Added unicode support for cmd.exe on Windows.

    .. versionadded:: 4.0
        Added the `err` parameter.

    """

    def prompt_func(text: str) -> str:
        f = hidden_prompt_func if hide_input else visible_prompt_func
        try:
            return _readline_prompt(f, text, err)
        except (KeyboardInterrupt, EOFError):
            # getpass doesn't print a newline if the user aborts input with ^C.
            # Allegedly this behavior is inherited from getpass(3).
            # A doc bug has been filed at https://bugs.python.org/issue24711
            if hide_input:
                echo(None, err=err)
            raise Abort() from None

    if value_proc is None:
        value_proc = convert_type(type, default)

    prompt = _build_prompt(
        text, prompt_suffix, show_default, default, show_choices, type
    )

    if confirmation_prompt:
        if confirmation_prompt is True:
            confirmation_prompt = _("Repeat for confirmation")

        confirmation_prompt = _build_prompt(confirmation_prompt, prompt_suffix)

    while True:
        while True:
            value = prompt_func(prompt)
            if value:
                break
            elif default is not None:
                # Defaults of any type are accepted and round trip through
                # value_proc like typed input, so the annotation is only
                # accurate for typed input.
                value = t.cast("str", default)
                break
        try:
            result = value_proc(value)
        except UsageError as e:
            message = _mask_hidden_input(e.message, value) if hide_input else e.message
            echo(_("Error: {message}").format(message=message), err=err)
            continue
        if not confirmation_prompt:
            return result
        while True:
            value2 = prompt_func(confirmation_prompt)
            is_empty = not value and not value2
            if value2 or is_empty:
                break
        if value == value2:
            return result
        echo(_("Error: The two entered values do not match."), err=err)


def confirm(
    text: str,
    default: bool | None = False,
    abort: bool = False,
    prompt_suffix: str = ": ",
    show_default: bool = True,
    err: bool = False,
) -> bool:
    """Prompts for confirmation (yes/no question).

    If the user aborts the input by sending a interrupt signal this
    function will catch it and raise a :exc:`Abort` exception.

    :param text: the question to ask.
    :param default: The default value to use when no input is given. If
        ``None``, repeat until input is given.
    :param abort: if this is set to `True` a negative answer aborts the
                  exception by raising :exc:`Abort`.
    :param prompt_suffix: a suffix that should be added to the prompt.
    :param show_default: shows or hides the default value in the prompt.
    :param err: if set to true the file defaults to ``stderr`` instead of
                ``stdout``, the same as with echo.

    .. versionchanged:: 8.3.1
        A space is no longer appended to the prompt.

    .. versionchanged:: 8.0
        Repeat until input is given if ``default`` is ``None``.

    .. versionadded:: 4.0
        Added the ``err`` parameter.
    """
    prompt = _build_prompt(
        text,
        prompt_suffix,
        show_default,
        "y/n" if default is None else ("Y/n" if default else "y/N"),
    )

    while True:
        try:
            value = _readline_prompt(visible_prompt_func, prompt, err).lower().strip()
        except (KeyboardInterrupt, EOFError):
            raise Abort() from None
        if value in ("y", "yes"):
            rv = True
        elif value in ("n", "no"):
            rv = False
        elif default is not None and value == "":
            rv = default
        else:
            echo(_("Error: invalid input"), err=err)
            continue
        break
    if abort and not rv:
        raise Abort()
    return rv


def get_pager_file(
    color: bool | None = None,
) -> t.ContextManager[t.TextIO]:
    """Context manager.

    Yields a writable file-like object which can be used as an output pager.

    .. versionadded:: 8.4.0

    :param color: controls if the pager supports ANSI colors or not.  The
                  default is autodetection.
    """
    from ._termui_impl import get_pager_file

    color = resolve_color_default(color)

    return get_pager_file(color=color)


def echo_via_pager(
    text_or_generator: cabc.Iterable[str] | t.Callable[[], cabc.Iterable[str]] | str,
    color: bool | None = None,
) -> None:
    """This function takes a text and shows it via an environment specific
    pager on stdout.

    .. versionchanged:: 3.0
       Added the `color` flag.

    :param text_or_generator: the text to page, or alternatively, a
                              generator emitting the text to page.
    :param color: controls if the pager supports ANSI colors or not.  The
                  default is autodetection.
    """

    if inspect.isgeneratorfunction(text_or_generator):
        i = t.cast("t.Callable[[], cabc.Iterable[str]]", text_or_generator)()
    elif isinstance(text_or_generator, str):
        i = [text_or_generator]
    else:
        i = iter(t.cast("cabc.Iterable[str]", text_or_generator))

    # convert every element of i to a text type if necessary
    text_generator = (el if isinstance(el, str) else str(el) for el in i)

    with get_pager_file(color=color) as pager:
        for text in itertools.chain(text_generator, "\n"):
            pager.write(text)
            # Flush after each write so a slow generator streams to the pager
            # incrementally rather than staying invisible until the pipe buffer
            # fills (~8 KB).
            pager.flush()


@t.overload
def progressbar(
    *,
    length: int,
    label: str | None = None,
    hidden: bool = False,
    show_eta: bool = True,
    show_percent: bool | None = None,
    show_pos: bool = False,
    fill_char: str = "#",
    empty_char: str = "-",
    bar_template: str = "%(label)s  [%(bar)s]  %(info)s",
    info_sep: str = "  ",
    width: int = 36,
    file: t.TextIO | None = None,
    color: bool | None = None,
    update_min_steps: int = 1,
) -> ProgressBar[int]: ...


@t.overload
def progressbar(
    iterable: cabc.Iterable[V] | None = None,
    length: int | None = None,
    label: str | None = None,
    hidden: bool = False,
    show_eta: bool = True,
    show_percent: bool | None = None,
    show_pos: bool = False,
    item_show_func: t.Callable[[V | None], str | None] | None = None,
    fill_char: str = "#",
    empty_char: str = "-",
    bar_template: str = "%(label)s  [%(bar)s]  %(info)s",
    info_sep: str = "  ",
    width: int = 36,
    file: t.TextIO | None = None,
    color: bool | None = None,
    update_min_steps: int = 1,
) -> ProgressBar[V]: ...


def progressbar(
    iterable: cabc.Iterable[V] | None = None,
    length: int | None = None,
    label: str | None = None,
    hidden: bool = False,
    show_eta: bool = True,
    show_percent: bool | None = None,
    show_pos: bool = False,
    item_show_func: t.Callable[[V | None], str | None] | None = None,
    fill_char: str = "#",
    empty_char: str = "-",
    bar_template: str = "%(label)s  [%(bar)s]  %(info)s",
    info_sep: str = "  ",
    width: int = 36,
    file: t.TextIO | None = None,
    color: bool | None = None,
    update_min_steps: int = 1,
) -> ProgressBar[V]:
    """This function creates an iterable context manager that can be used
    to iterate over something while showing a progress bar.  It will
    either iterate over the `iterable` or `length` items (that are counted
    up).  While iteration happens, this function will print a rendered
    progress bar to the given `file` (defaults to stdout) and will attempt
    to calculate remaining time and more.  By default, this progress bar
    will not be rendered if the file is not a terminal.

    The context manager creates the progress bar.  When the context
    manager is entered the progress bar is already created.  With every
    iteration over the progress bar, the iterable passed to the bar is
    advanced and the bar is updated.  When the context manager exits,
    a newline is printed and the progress bar is finalized on screen.

    Note: The progress bar is currently designed for use cases where the
    total progress can be expected to take at least several seconds.
    Because of this, the ProgressBar class object won't display
    progress that is considered too fast, and progress where the time
    between steps is less than a second.

    No printing must happen or the progress bar will be unintentionally
    destroyed.

    Example usage::

        with progressbar(items) as bar:
            for item in bar:
                do_something_with(item)

    Alternatively, if no iterable is specified, one can manually update the
    progress bar through the `update()` method instead of directly
