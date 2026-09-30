# ------------------------------------------------------------------------------
# pycparser: c_lexer.py
#
# CLexer class: lexer for the C language
#
# Eli Bendersky [https://eli.thegreenplace.net/]
# License: BSD
# ------------------------------------------------------------------------------
import re
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Dict, List, Optional, Tuple


@dataclass(slots=True)
class _Token:
    type: str
    value: str
    lineno: int
    column: int


class CLexer:
    """A standalone lexer for C.

    Parameters for construction:
        error_func:
            Called with (msg, line, column) on lexing errors.
        on_lbrace_func:
            Called when an LBRACE token is produced (used for scope tracking).
        on_rbrace_func:
            Called when an RBRACE token is produced (used for scope tracking).
        type_lookup_func:
            Called with an identifier name; expected to return True if it is
            a typedef name and should be tokenized as TYPEID.

    Call input(text) to initialize lexing, and then keep calling token() to
    get the next token, until it returns None (at end of input).
    """

    def __init__(
        self,
        error_func: Callable[[str, int, int], None],
        on_lbrace_func: Callable[[], None],
        on_rbrace_func: Callable[[], None],
        type_lookup_func: Callable[[str], bool],
    ) -> None:
        self.error_func = error_func
        self.on_lbrace_func = on_lbrace_func
        self.on_rbrace_func = on_rbrace_func
        self.type_lookup_func = type_lookup_func
        self._init_state()

    def input(self, text: str, filename: str = "") -> None:
        """Initialize the lexer to the given input text.

        filename is an optional name identifying the file from which the input
        comes. The lexer can modify it if #line directives are encountered.
        """
        self._init_state()
        self._lexdata = text
        self._filename = filename

    def _init_state(self) -> None:
        self._lexdata = ""
        self._filename = ""
        self._pos = 0
        self._line_start = 0
        self._pending_tok: Optional[_Token] = None
        self._lineno = 1

    @property
    def filename(self) -> str:
        return self._filename

    def token(self) -> Optional[_Token]:
        # Lexing strategy overview:
        #
        # - We maintain a current position (self._pos), line number, and the
        #   byte offset of the current line start. The lexer is a simple loop
        #   that skips whitespace/newlines and emits one token per call.
        # - A small amount of logic is handled manually before regex matching:
        #
        #   * Preprocessor-style directives: if we see '#', we check whether
        #     it's a #line or #pragma directive and consume it inline. #line
        #     updates lineno/filename and produces no tokens. #pragma can yield
        #     both PPPRAGMA and PPPRAGMASTR, but token() returns a single token,
        #     so we stash the PPPRAGMASTR as _pending_tok to return on the next
        #     token() call. Otherwise we return PPHASH.
        #   * Newlines update lineno/line-start tracking so tokens can record
        #     accurate columns.
        #
        # - The bulk of tokens are recognized in _match_token:
        #
        #   * _regex_rules: regex patterns for identifiers, literals, and other
        #     complex tokens (including error-producing patterns). The lexer
        #     uses a combined _regex_master to scan options at the same time.
        #   * _fixed_tokens: exact string matches for operators and punctuation,
        #     resolved by longest match.
        #
        # - Error patterns call the error callback and advance minimally, which
        #   keeps lexing resilient while reporting useful diagnostics.
        text = self._lexdata
        n = len(text)

        if self._pending_tok is not None:
            tok = self._pending_tok
            self._pending_tok = None
            return tok

        while self._pos < n:
            match text[self._pos]:
                case " " | "\t":
                    self._pos += 1
                case "\n":
                    self._lineno += 1
                    self._pos += 1
                    self._line_start = self._pos
                case "#":
                    if _line_pattern.match(text, self._pos + 1):
                        self._pos += 1
                        self._handle_ppline()
                        continue
                    if _pragma_pattern.match(text, self._pos + 1):
                        self._pos += 1
                        toks = self._handle_pppragma()
                        if len(toks) > 1:
                            self._pending_tok = toks[1]
                        if len(toks) > 0:
                            return toks[0]
                        continue
                    tok = self._make_token("PPHASH", "#", self._pos)
                    self._pos += 1
                    return tok
                case _:
                    if tok := self._match_token():
                        return tok
                    else:
                        continue

    def _match_token(self) -> Optional[_Token]:
        """Match one token at the current position.

        Returns a Token on success, or None if no token could be matched and
        an error was reported. This method always advances _pos by the matched
        length, or by 1 on error/no-match.
        """
        text = self._lexdata
        pos = self._pos
        # We pick the longest match between:
        # - the master regex (identifiers, literals, error patterns, etc.)
        # - fixed operator/punctuator literals from the bucket for text[pos]
        #
        # The longest match is required to ensure we properly lex something
        # like ".123" (a floating-point constant) as a single entity (with
        # FLOAT_CONST), rather than a PERIOD followed by a number.
        #
        # The fixed-literal buckets are already length-sorted, so within that
        # bucket we can take the first match. However, we still compare its
        # length to the regex match because the regex may have matched a longer
        # token that should take precedence.
        best = None

        if m := _regex_master.match(text, pos):
            tok_type = m.lastgroup
            # All master-regex alternatives are named; lastgroup shouldn't be None.
            assert tok_type is not None
            value = m.group(tok_type)
            length = len(value)
            action, msg = _regex_actions[tok_type]
            best = (length, tok_type, value, action, msg)

        if bucket := _fixed_tokens_by_first.get(text[pos]):
            for entry in bucket:
                if text.startswith(entry.literal, pos):
                    length = len(entry.literal)
                    if best is None or length > best[0]:
                        best = (
                            length,
                            entry.tok_type,
                            entry.literal,
                            _RegexAction.TOKEN,
                            None,
                        )
                    break

        if best is None:
            msg = f"Illegal character {repr(text[pos])}"
            self._error(msg, pos)
            self._pos += 1
            return None

        length, tok_type, value, action, msg = best
        if action == _RegexAction.ERROR:
            if tok_type == "BAD_CHAR_CONST":
                msg = f"Invalid char constant {value}"
            # All other ERROR rules provide a message.
            assert msg is not None
            self._error(msg, pos)
            self._pos += max(1, length)
            return None

        if action == _RegexAction.ID:
            tok_type = _keyword_map.get(value, "ID")
            if tok_type == "ID" and self.type_lookup_func(value):
                tok_type = "TYPEID"

        tok = self._make_token(tok_type, value, pos)
        self._pos += length

        if tok.type == "LBRACE":
            self.on_lbrace_func()
        elif tok.type == "RBRACE":
            self.on_rbrace_func()

        return tok

    def _make_token(self, tok_type: str, value: str, pos: int) -> _Token:
        """Create a Token at an absolute input position.

        Expects tok_type/value and the absolute byte offset pos in the current
        input. Does not advance lexer state; callers manage _pos themselves.
        Returns a Token with lineno/column computed from current line tracking.
        """
        column = pos - self._line_start + 1
        tok = _Token(tok_type, value, self._lineno, column)
        return tok

    def _error(self, msg: str, pos: int) -> None:
        column = pos - self._line_start + 1
        self.error_func(msg, self._lineno, column)

    def _handle_ppline(self) -> None:
        # Since #line directives aren't supposed to return tokens but should
        # only affect the lexer's state (update line/filename for coords), this
        # method does a bit of parsing on its own. It doesn't return anything,
        # but its side effect is to update self._pos past the directive, and
        # potentially update self._lineno and self._filename, based on the
        # directive's contents.
        #
        # Accepted #line forms from preprocessors:
        # - "#line 66 \"kwas\\df.h\""
        # - "# 9"
        # - "#line 10 \"include/me.h\" 1 2 3" (extra numeric flags)
        # - "# 1 \"file.h\" 3"
        # Errors we must report:
        # - "#line \"file.h\"" (filename before line number)
        # - "#line df" (garbage instead of number/string)
        #
        # We scan the directive line once (after an optional 'line' keyword),
        # validating the order: NUMBER, optional STRING, then any NUMBERs.
        # The NUMBERs tail is only accepted if a filename STRING was present.
        text = self._lexdata
        n = len(text)
        line_end = text.find("\n", self._pos)
        if line_end == -1:
            line_end = n
        line = text[self._pos : line_end]
        pos = 0
        line_len = len(line)

        def skip_ws() -> None:
            nonlocal pos
            while pos < line_len and line[pos] in " \t":
                pos += 1

        skip_ws()
        if line.startswith("line", pos):
            pos += 4

        def success(pp_line: Optional[str], pp_filename: Optional[str]) -> None:
            if pp_line is None:
                self._error("line number missing in #line", self._pos + line_len)
            else:
                self._lineno = int(pp_line)
                if pp_filename is not None:
                    self._filename = pp_filename
            self._pos = line_end + 1
            self._line_start = self._pos

        def fail(msg: str, offset: int) -> None:
            self._error(msg, self._pos + offset)
            self._pos = line_end + 1
            self._line_start = self._pos

        skip_ws()
        if pos >= line_len:
            success(None, None)
            return
        if line[pos] == '"':
            fail("filename before line number in #line", pos)
            return

        m = re.match(_decimal_constant, line[pos:])
        if not m:
            fail("invalid #line directive", pos)
            return

        pp_line = m.group(0)
        pos += len(pp_line)
        skip_ws()
        if pos >= line_len:
            success(pp_line, None)
            return

        if line[pos] != '"':
            fail("invalid #line directive", pos)
            return

        m = re.match(_string_literal, line[pos:])
        if not m:
            fail("invalid #line directive", pos)
            return

        pp_filename = m.group(0).lstrip('"').rstrip('"')
        pos += len(m.group(0))

        # Consume arbitrary sequence of numeric flags after the directive
        while True:
            skip_ws()
            if pos >= line_len:
                break
            m = re.match(_decimal_constant, line[pos:])
            if not m:
                fail("invalid #line directive", pos)
                return
            pos += len(m.group(0))

        success(pp_line, pp_filename)

    def _handle_pppragma(self) -> List[_Token]:
        # Parse a full #pragma line; returns a list of tokens with 1 or 2
        # tokens - PPPRAGMA and an optional PPPRAGMASTR. If an empty list is
        # returned, it means an error occurred, or we're at the end of input.
        #
        # Examples:
        # - "#pragma" -> PPPRAGMA only
        # - "#pragma once" -> PPPRAGMA, PPPRAGMASTR("once")
        # - "# pragma omp parallel private(th_id)" -> PPPRAGMA, PPPRAGMASTR("omp parallel private(th_id)")
        # - "#\tpragma {pack: 2, smack: 3}" -> PPPRAGMA, PPPRAGMASTR("{pack: 2, smack: 3}")
        text = self._lexdata
        n = len(text)
        pos = self._pos

        while pos < n and text[pos] in " \t":
            pos += 1
        if pos >= n:
            self._pos = pos
            return []

        if not text.startswith("pragma", pos):
            self._error("invalid #pragma directive", pos)
            self._pos = pos + 1
            return []

        pragma_pos = pos
        pos += len("pragma")
        toks = [self._make_token("PPPRAGMA", "pragma", pragma_pos)]

        while pos < n and text[pos] in " \t":
            pos += 1

        start = pos
        while pos < n and text[pos] != "\n":
            pos += 1
        if pos > start:
            toks.append(self._make_token("PPPRAGMASTR", text[start:pos], start))
        if pos < n and text[pos] == "\n":
            self._lineno += 1
            pos += 1
            self._line_start = pos
        self._pos = pos
        return toks


##
## Reserved keywords
##
_keywords: Tuple[str, ...] = (
    "AUTO",
    "BREAK",
    "CASE",
    "CHAR",
    "CONST",
    "CONTINUE",
    "DEFAULT",
    "DO",
    "DOUBLE",
    "ELSE",
    "ENUM",
    "EXTERN",
    "FLOAT",
    "FOR",
    "GOTO",
    "IF",
    "INLINE",
    "INT",
    "LONG",
    "REGISTER",
    "OFFSETOF",
    "RESTRICT",
    "RETURN",
    "SHORT",
    "SIGNED",
    "SIZEOF",
    "STATIC",
    "STRUCT",
    "SWITCH",
    "TYPEDEF",
    "UNION",
    "UNSIGNED",
    "VOID",
    "VOLATILE",
    "WHILE",
    "__INT128",
    "_BOOL",
    "_COMPLEX",
    "_NORETURN",
    "_THREAD_LOCAL",
    "_STATIC_ASSERT",
    "_ATOMIC",
    "_ALIGNOF",
    "_ALIGNAS",
    "_PRAGMA",
)

_keyword_map: Dict[str, str] = {}

for keyword in _keywords:
    # Keywords from new C standard are mixed-case, like _Bool, _Alignas, etc.
    if keyword.startswith("_") and len(keyword) > 1 and keyword[1].isalpha():
        _keyword_map[keyword[:2].upper() + keyword[2:].lower()] = keyword
    else:
        _keyword_map[keyword.lower()] = keyword

##
## Regexes for use in tokens
##

# valid C identifiers (K&R2: A.2.3), plus '$' (supported by some compilers)
_identifier = r"[a-zA-Z_$][0-9a-zA-Z_$]*"

_hex_prefix = "0[xX]"
_hex_digits = "[0-9a-fA-F]+"
_bin_prefix = "0[bB]"
_bin_digits = "[01]+"

# integer constants (K&R2: A.2.5.1)
_integer_suffix_opt = (
    r"(([uU]ll)|([uU]LL)|(ll[uU]?)|(LL[uU]?)|([uU][lL])|([lL][uU]?)|[uU])?"
)
_decimal_constant = (
    "(0" + _integer_suffix_opt + ")|([1-9][0-9]*" + _integer_suffix_opt + ")"
)
_octal_constant = "0[0-7]*" + _integer_suffix_opt
_hex_constant = _hex_prefix + _hex_digits + _integer_suffix_opt
_bin_constant = _bin_prefix + _bin_digits + _integer_suffix_opt

_bad_octal_constant = "0[0-7]*[89]"

# comments are not supported
_unsupported_c_style_comment = r"\/\*"
_unsupported_cxx_style_comment = r"\/\/"

# character constants (K&R2: A.2.5.2)
# Note: a-zA-Z and '.-~^_!=&;,' are allowed as escape chars to support #line
# directives with Windows paths as filenames (..\..\dir\file)
# For the same reason, decimal_escape allows all digit sequences. We want to
# parse all correct code, even if it means to sometimes parse incorrect
# code.
#
# The original regexes were taken verbatim from the C syntax definition,
# and were later modified to avoid worst-case exponential running time.
#
#   simple_escape = r"""([a-zA-Z._~!=&\^\-\\?'"])"""
#   decimal_escape = r"""(\d+)"""
#   hex_escape = r"""(x[0-9a-fA-F]+)"""
#   bad_escape = r"""([\\][^a-zA-Z._~^!=&\^\-\\?'"x0-7])"""
#
