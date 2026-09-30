#   Copyright 2000-2010 Michael Hudson-Doyle <micahel@gmail.com>
#                       Antonio Cuni
#                       Armin Rigo
#
#                        All Rights Reserved
#
#
# Permission to use, copy, modify, and distribute this software and
# its documentation for any purpose is hereby granted without fee,
# provided that the above copyright notice appear in all copies and
# that both that copyright notice and this permission notice appear in
# supporting documentation.
#
# THE AUTHOR MICHAEL HUDSON DISCLAIMS ALL WARRANTIES WITH REGARD TO
# THIS SOFTWARE, INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY
# AND FITNESS, IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL,
# INDIRECT OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER
# RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF
# CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF OR IN
# CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.

from __future__ import annotations

import sys
import _colorize

from contextlib import contextmanager
from dataclasses import dataclass, field, fields

from . import commands, console, input
from .utils import wlen, unbracket, disp_str, gen_colors, THEME
from .trace import trace


# types
Command = commands.Command
from .types import Callback, SimpleContextManager, KeySpec, CommandName


# syntax classes
SYNTAX_WHITESPACE, SYNTAX_WORD, SYNTAX_SYMBOL = range(3)


def make_default_syntax_table() -> dict[str, int]:
    # XXX perhaps should use some unicodedata here?
    st: dict[str, int] = {}
    for c in map(chr, range(256)):
        st[c] = SYNTAX_SYMBOL
    for c in [a for a in map(chr, range(256)) if a.isalnum()]:
        st[c] = SYNTAX_WORD
    st["\n"] = st[" "] = SYNTAX_WHITESPACE
    return st


def make_default_commands() -> dict[CommandName, type[Command]]:
    result: dict[CommandName, type[Command]] = {}
    for v in vars(commands).values():
        if isinstance(v, type) and issubclass(v, Command) and v.__name__[0].islower():
            result[v.__name__] = v
            result[v.__name__.replace("_", "-")] = v
    return result


default_keymap: tuple[tuple[KeySpec, CommandName], ...] = tuple(
    [
        (r"\C-a", "beginning-of-line"),
        (r"\C-b", "left"),
        (r"\C-c", "interrupt"),
        (r"\C-d", "delete"),
        (r"\C-e", "end-of-line"),
        (r"\C-f", "right"),
        (r"\C-g", "cancel"),
        (r"\C-h", "backspace"),
        (r"\C-j", "accept"),
        (r"\<return>", "accept"),
        (r"\C-k", "kill-line"),
        (r"\C-l", "clear-screen"),
        (r"\C-m", "accept"),
        (r"\C-t", "transpose-characters"),
        (r"\C-u", "unix-line-discard"),
        (r"\C-w", "unix-word-rubout"),
        (r"\C-x\C-u", "upcase-region"),
        (r"\C-y", "yank"),
        *(() if sys.platform == "win32" else ((r"\C-z", "suspend"), )),
        (r"\M-b", "backward-word"),
        (r"\M-c", "capitalize-word"),
        (r"\M-d", "kill-word"),
        (r"\M-f", "forward-word"),
        (r"\M-l", "downcase-word"),
        (r"\M-t", "transpose-words"),
        (r"\M-u", "upcase-word"),
        (r"\M-y", "yank-pop"),
        (r"\M--", "digit-arg"),
        (r"\M-0", "digit-arg"),
        (r"\M-1", "digit-arg"),
        (r"\M-2", "digit-arg"),
        (r"\M-3", "digit-arg"),
        (r"\M-4", "digit-arg"),
        (r"\M-5", "digit-arg"),
        (r"\M-6", "digit-arg"),
        (r"\M-7", "digit-arg"),
        (r"\M-8", "digit-arg"),
        (r"\M-9", "digit-arg"),
        (r"\M-\n", "accept"),
        ("\\\\", "self-insert"),
        (r"\x1b[200~", "perform-bracketed-paste"),
        (r"\x03", "ctrl-c"),
    ]
    + [(c, "self-insert") for c in map(chr, range(32, 127)) if c != "\\"]
    + [(c, "self-insert") for c in map(chr, range(128, 256)) if c.isalpha()]
    + [
        (r"\<up>", "up"),
        (r"\<down>", "down"),
        (r"\<left>", "left"),
        (r"\C-\<left>", "backward-word"),
        (r"\<right>", "right"),
        (r"\C-\<right>", "forward-word"),
        (r"\<delete>", "delete"),
        (r"\x1b[3~", "delete"),
        (r"\<backspace>", "backspace"),
        (r"\M-\<backspace>", "backward-kill-word"),
        (r"\<end>", "end-of-line"),  # was 'end'
        (r"\<home>", "beginning-of-line"),  # was 'home'
        (r"\<f1>", "help"),
        (r"\<f2>", "show-history"),
        (r"\<f3>", "paste-mode"),
        (r"\EOF", "end"),  # the entries in the terminfo database for xterms
        (r"\EOH", "home"),  # seem to be wrong.  this is a less than ideal
        # workaround
    ]
)


@dataclass(slots=True)
class Reader:
    """The Reader class implements the bare bones of a command reader,
    handling such details as editing and cursor motion.  What it does
    not support are such things as completion or history support -
    these are implemented elsewhere.

    Instance variables of note include:

      * buffer:
        A per-character list containing all the characters that have been
        entered. Does not include color information.
      * console:
        Hopefully encapsulates the OS dependent stuff.
      * pos:
        A 0-based index into 'buffer' for where the insertion point
        is.
      * screeninfo:
        A list of screen position tuples. Each list element is a tuple
        representing information on visible line length for a given line.
        Allows for efficient skipping of color escape sequences.
      * cxy, lxy:
        the position of the insertion point in screen ...
      * syntax_table:
        Dictionary mapping characters to 'syntax class'; read the
        emacs docs to see what this means :-)
      * commands:
        Dictionary mapping command names to command classes.
      * arg:
        The emacs-style prefix argument.  It will be None if no such
        argument has been provided.
      * dirty:
        True if we need to refresh the display.
      * kill_ring:
        The emacs-style kill-ring; manipulated with yank & yank-pop
      * ps1, ps2, ps3, ps4:
        prompts.  ps1 is the prompt for a one-line input; for a
        multiline input it looks like:
            ps2> first line of input goes here
            ps3> second and further
            ps3> lines get ps3
            ...
            ps4> and the last one gets ps4
        As with the usual top-level, you can set these to instances if
        you like; str() will be called on them (once) at the beginning
        of each command.  Don't put really long or newline containing
        strings here, please!
        This is just the default policy; you can change it freely by
        overriding get_prompt() (and indeed some standard subclasses
        do).
      * finished:
        handle1 will set this to a true value if a command signals
        that we're done.
    """

    console: console.Console

    ## state
    buffer: list[str] = field(default_factory=list)
    pos: int = 0
    ps1: str = "->> "
    ps2: str = "/>> "
    ps3: str = "|.. "
    ps4: str = R"\__ "
    kill_ring: list[list[str]] = field(default_factory=list)
    msg: str = ""
    arg: int | None = None
    dirty: bool = False
    finished: bool = False
    paste_mode: bool = False
    commands: dict[str, type[Command]] = field(default_factory=make_default_commands)
    last_command: type[Command] | None = None
    syntax_table: dict[str, int] = field(default_factory=make_default_syntax_table)
    keymap: tuple[tuple[str, str], ...] = ()
    input_trans: input.KeymapTranslator = field(init=False)
    input_trans_stack: list[input.KeymapTranslator] = field(default_factory=list)
    screen: list[str] = field(default_factory=list)
    screeninfo: list[tuple[int, list[int]]] = field(init=False)
    cxy: tuple[int, int] = field(init=False)
    lxy: tuple[int, int] = field(init=False)
    scheduled_commands: list[str] = field(default_factory=list)
    can_colorize: bool = False
    threading_hook: Callback | None = None

    ## cached metadata to speed up screen refreshes
    @dataclass
    class RefreshCache:
        screen: list[str] = field(default_factory=list)
        screeninfo: list[tuple[int, list[int]]] = field(init=False)
        line_end_offsets: list[int] = field(default_factory=list)
        pos: int = field(init=False)
        cxy: tuple[int, int] = field(init=False)
        dimensions: tuple[int, int] = field(init=False)
        invalidated: bool = False

        def update_cache(self,
                         reader: Reader,
                         screen: list[str],
                         screeninfo: list[tuple[int, list[int]]],
            ) -> None:
            self.screen = screen.copy()
            self.screeninfo = screeninfo.copy()
            self.pos = reader.pos
            self.cxy = reader.cxy
            self.dimensions = reader.console.width, reader.console.height
            self.invalidated = False

        def valid(self, reader: Reader) -> bool:
            if self.invalidated:
                return False
            dimensions = reader.console.width, reader.console.height
            dimensions_changed = dimensions != self.dimensions
            return not dimensions_changed

        def get_cached_location(self, reader: Reader) -> tuple[int, int]:
            if self.invalidated:
                raise ValueError("Cache is invalidated")
            offset = 0
            earliest_common_pos = min(reader.pos, self.pos)
            num_common_lines = len(self.line_end_offsets)
            while num_common_lines > 0:
                offset = self.line_end_offsets[num_common_lines - 1]
                if earliest_common_pos > offset:
                    break
                num_common_lines -= 1
            else:
                offset = 0
            return offset, num_common_lines

    last_refresh_cache: RefreshCache = field(default_factory=RefreshCache)

    def __post_init__(self) -> None:
        # Enable the use of `insert` without a `prepare` call - necessary to
        # facilitate the tab completion hack implemented for
        # <https://bugs.python.org/issue25660>.
        self.keymap = self.collect_keymap()
        self.input_trans = input.KeymapTranslator(
            self.keymap, invalid_cls="invalid-key", character_cls="self-insert"
        )
        self.screeninfo = [(0, [])]
        self.cxy = self.pos2xy()
        self.lxy = (self.pos, 0)
        self.can_colorize = _colorize.can_colorize()

        self.last_refresh_cache.screeninfo = self.screeninfo
        self.last_refresh_cache.pos = self.pos
        self.last_refresh_cache.cxy = self.cxy
        self.last_refresh_cache.dimensions = (0, 0)

    def collect_keymap(self) -> tuple[tuple[KeySpec, CommandName], ...]:
        return default_keymap

    def calc_screen(self) -> list[str]:
        """Translate changes in self.buffer into changes in self.console.screen."""
        # Since the last call to calc_screen:
        # screen and screeninfo may differ due to a completion menu being shown
        # pos and cxy may differ due to edits, cursor movements, or completion menus

        # Lines that are above both the old and new cursor position can't have changed,
        # unless the terminal has been resized (which might cause reflowing) or we've
        # entered or left paste mode (which changes prompts, causing reflowing).
        num_common_lines = 0
        offset = 0
        if self.last_refresh_cache.valid(self):
            offset, num_common_lines = self.last_refresh_cache.get_cached_location(self)

        screen = self.last_refresh_cache.screen
        del screen[num_common_lines:]

        screeninfo = self.last_refresh_cache.screeninfo
        del screeninfo[num_common_lines:]

        last_refresh_line_end_offsets = self.last_refresh_cache.line_end_offsets
        del last_refresh_line_end_offsets[num_common_lines:]

        pos = self.pos
        pos -= offset

        prompt_from_cache = (offset and self.buffer[offset - 1] != "\n")

        if self.can_colorize:
            colors = list(gen_colors(self.get_unicode()))
        else:
            colors = None
        trace("colors = {colors}", colors=colors)
        lines = "".join(self.buffer[offset:]).split("\n")
        cursor_found = False
        lines_beyond_cursor = 0
        for ln, line in enumerate(lines, num_common_lines):
            line_len = len(line)
            if 0 <= pos <= line_len:
                self.lxy = pos, ln
                cursor_found = True
            elif cursor_found:
                lines_beyond_cursor += 1
                if lines_beyond_cursor > self.console.height:
                    # No need to keep formatting lines.
                    # The console can't show them.
                    break
            if prompt_from_cache:
                # Only the first line's prompt can come from the cache
                prompt_from_cache = False
                prompt = ""
            else:
                prompt = self.get_prompt(ln, line_len >= pos >= 0)
            while "\n" in prompt:
                pre_prompt, _, prompt = prompt.partition("\n")
                last_refresh_line_end_offsets.append(offset)
                screen.append(pre_prompt)
                screeninfo.append((0, []))
            pos -= line_len + 1
            prompt, prompt_len = self.process_prompt(prompt)
            chars, char_widths = disp_str(line, colors, offset)
            wrapcount = (sum(char_widths) + prompt_len) // self.console.width
            if wrapcount == 0 or not char_widths:
                offset += line_len + 1  # Takes all of the line plus the newline
                last_refresh_line_end_offsets.append(offset)
                screen.append(prompt + "".join(chars))
                screeninfo.append((prompt_len, char_widths))
            else:
                pre = prompt
                prelen = prompt_len
                for wrap in range(wrapcount + 1):
                    index_to_wrap_before = 0
                    column = 0
                    for char_width in char_widths:
                        if column + char_width + prelen >= self.console.width:
                            break
                        index_to_wrap_before += 1
                        column += char_width
                    if len(chars) > index_to_wrap_before:
                        offset += index_to_wrap_before
                        post = "\\"
                        after = [1]
                    else:
                        offset += index_to_wrap_before + 1  # Takes the newline
                        post = ""
                        after = []
                    last_refresh_line_end_offsets.append(offset)
                    render = pre + "".join(chars[:index_to_wrap_before]) + post
                    render_widths = char_widths[:index_to_wrap_before] + after
                    screen.append(render)
                    screeninfo.append((prelen, render_widths))
                    chars = chars[index_to_wrap_before:]
                    char_widths = char_widths[index_to_wrap_before:]
                    pre = ""
                    prelen = 0
        self.screeninfo = screeninfo
        self.cxy = self.pos2xy()
        if self.msg:
            for mline in self.msg.split("\n"):
                screen.append(mline)
                screeninfo.append((0, []))

        self.last_refresh_cache.update_cache(self, screen, screeninfo)
        return screen

    @staticmethod
    def process_prompt(prompt: str) -> tuple[str, int]:
        r"""Return a tuple with the prompt string and its visible length.

        The prompt string has the zero-width brackets recognized by shells
        (\x01 and \x02) removed.  The length ignores anything between those
        brackets as well as any ANSI escape sequences.
        """
        out_prompt = unbracket(prompt, including_content=False)
        visible_prompt = unbracket(prompt, including_content=True)
        return out_prompt, wlen(visible_prompt)

    def bow(self, p: int | None = None) -> int:
        """Return the 0-based index of the word break preceding p most
        immediately.

        p defaults to self.pos; word boundaries are determined using
        self.syntax_table."""
        if p is None:
            p = self.pos
        st = self.syntax_table
        b = self.buffer
        p -= 1
        while p >= 0 and st.get(b[p], SYNTAX_WORD) != SYNTAX_WORD:
