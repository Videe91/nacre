"""
Functionality: Find credential values by proximity to a credential word, whatever the syntax between them.
Owns: the credential-word list, the two proximity forms (same statement; first non-empty line after a string or
  heredoc opener), the candidate-token checks (length, digit, entropy, not an identifier, not an alphabet constant).
Public entry: proximity_findings(), RULE_ID
Decisions: D-0011, D-0007
Assumptions: A-0010, A-0044
Notes: Owner, 2026-10-02 (gate item 12, after H4 and H5 failed on shape enumeration): additive to the
  `credential-slot-value` rule, which stays. Called by strip_secrets.py as layer 4; its findings merge like any other.
  - P1, same statement: after a credential word, the first qualifying token that starts within 100 characters, before
    the end of the line or a ';'. A run of value characters is split at internal '=' (a=b=VALUE; trailing '=' is
    base64 padding), so `Password=VALUE` yields VALUE.
  - P1 is tried first; P2 only when the statement holds no qualifying token.
  - P2, after an opener: an opener (string quote, triple quote, raw-string or heredoc start; the same documented forms
    as scripts/build_credential_slot_regex.py) on the credential word's line or the next one, with at most 60
    characters after it on its line; the candidate is the first token of the first non-empty line after it.
  - The credential word must be a whole identifier component (_whole_word) and, in P1, the value must lie in the word's
    own field (_field_end: no ', "' or brace in between). Both added 2026-10-02 on working data (the repository's own
    JSON results and docs: 'auth' inside 'authoritative', 'tokens' counts, gaps crossing JSON fields).
  - A token right after '_' or '.' is the tail of a larger identifier or name (i_<hex>, docs.example.org/...), not a
    value (P1; 2026-10-02, working data).
  - Nearest label wins (P1): a value directly after its own non-credential label is skipped. A value containing '/'
    with no uppercase letter is a path or name. Both 2026-10-02, working data (repository JSON and docs).
  - A qualifying token: 16-128 chars of [A-Za-z0-9+/=~-], contains a digit (A-0044), entropy > 3.0 bits/char, does
    NOT itself contain a credential word (then it is an identifier such as X25519PrivateKey), and is not a contiguous
    run of a standard alphabet (constants like "0123456789abcdef"). The last two checks were added on working data
    (2026-10-02: cryptography class names and a hex-alphabet constant). Tightening only.
  - Linear time (2026-10-02): every per-word step works on a bounded slice (the re2 binding copies its whole input on
    each call, so passing the full text per word was quadratic: 320 kB took 18.8 s). Lines longer than LINE_LIMIT
    cannot be opener lines; at most MAX_BLANK_LINES blank lines are skipped after an opener.
  - Admission (D-0011): source = owner decision 2026-10-02 (gate item 12 re-plan); test =
    tests/ledger/test_find_credential_proximity.py; holdout = H6 (pre-registered, measured once).
"""
import math
from collections import Counter

import re2

RULE_ID = "credential-proximity-value"
WORD = re2.compile(
    r"(?i)(?:password|passwd|pwd|passphrase|secret|token|api[_\-]?key|apikey|access[_\-]?key|private[_\-]?key"
    r"|authorization|auth|credentials?|cred|(?:signing|encryption|master|session|hmac|shared|webhook)[_\-]?key)")
_RUN = re2.compile(r"[A-Za-z0-9+/=\-~]+")
_LABEL = re2.compile(r"([A-Za-z_][A-Za-z0-9_.\-]*)[\"']?\s*(?:=|:|=>)\s*[\"'`]?\s*$")   # a label right before a value
_LONG_RUN = re2.compile(r"[A-Za-z0-9+/=\-~]{16,}")     # a qualifying piece (>= 16) lies inside a run of >= 16
_OPENER = re2.compile(
    r"""(?:[A-Za-z~$]{0,4}(?:"{3,}|'{3})|#+"{1,3}|[rRbB]{1,2}#*"|R"[^\s()\\]{0,16}\(|`|@["']"""
    r"""|<<[~\-]?["'`]?[A-Za-z_][A-Za-z0-9_]*["'`]?|<<<\s*["']?[A-Za-z_][A-Za-z0-9_]*["']?|\[=*\[|''|["'])""")
_ALPHABETS = ("0123456789abcdefghijklmnopqrstuvwxyz", "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ",
              "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+/",
              "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/",
              "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ",
              "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")
GAP, MIN_LEN, MAX_LEN, MIN_BITS, OPENER_TAIL = 100, 16, 128, 3.0, 60
OPENER_MAX, LINE_LIMIT, MAX_BLANK_LINES = 80, 4096, 20   # bounds: linear time on any input (D1)


def _entropy(s: str) -> float:
    counts, n = Counter(s), len(s)
    return -sum(c / n * math.log2(c / n) for c in counts.values())


def _qualifies(tok: str) -> bool:
    return (MIN_LEN <= len(tok) <= MAX_LEN and any(c.isdigit() for c in tok) and _entropy(tok) > MIN_BITS
            and not WORD.search(tok) and not any(tok in a for a in _ALPHABETS)
            and not ("/" in tok and not any(c.isupper() for c in tok)))      # a lowercase path is a name


def _own_label_is_other(text: str, we: int, s: int) -> bool:
    """Nearest label wins: the value directly follows its own `name=` / `name:` label, and that name is not a
    credential word, so the value belongs to that label (span_sha256=<hex>, "model": "<id>")."""
    m = _LABEL.search(text[we:s])
    return bool(m) and not WORD.search(m.group(1))


def _pieces(seg: str, base: int):
    """Candidate (start, end) tokens of `seg` (offsets + base): maximal value-char runs split at internal '='."""
    for m in _LONG_RUN.finditer(seg):
        s, e = m.span()
        core_end = e
        while core_end > s and seg[core_end - 1] == "=":
            core_end -= 1                                  # trailing padding stays with the last piece
        cut = s
        for i in range(s, core_end):
            if seg[i] == "=":
                if i > cut:
                    yield base + cut, base + i
                cut = i + 1
        if cut < e:
            yield base + cut, base + e


def _line_end(text: str, i: int) -> int | None:
    """End of the line starting at or containing i, searched at most LINE_LIMIT chars ahead (None: too long)."""
    j = text.find("\n", i, i + LINE_LIMIT)
    if j != -1:
        return j
    return len(text) if len(text) - i <= LINE_LIMIT else None


def _same_statement(text: str, we: int):
    seg = text[we:we + GAP + MAX_LEN + 1]                  # every operation on a bounded slice (no O(n) per word)
    for stop_char in "\n;":
        k = seg.find(stop_char)
        if k != -1:
            seg = seg[:k]
    seg = seg[:_field_end(seg)]
    for s, e in _pieces(seg, we):
        if (s - we <= GAP and text[s - 1] not in "_." and _qualifies(text[s:e])
                and not _own_label_is_other(text, we, s)):
            return s, e
    return None


def _first_token_after(text: str, line_end: int):
    """The first value-char run of the first non-empty line after the line ending at `line_end`, if it qualifies."""
    i, blank = line_end + 1, 0
    while i < len(text) and blank < MAX_BLANK_LINES:
        end = _line_end(text, i)
        if end is None or text[i:end].strip():
            break
        i, blank = end + 1, blank + 1                      # skip blank lines
    head = text[i:i + MAX_LEN + 64].lstrip(" \t")
    m = _RUN.match(head)
    if not m:
        return None
    s = i + (len(text[i:i + MAX_LEN + 64]) - len(head))
    return (s, s + m.end()) if _qualifies(head[:m.end()]) else None


def _after_opener(text: str, we: int):
    """P2: an opener on the word's line or the next, then the first token of the next non-empty line. Each line with an
    opener near its end is tried in turn (e.g. key="SigningKey" then value=" on the next line)."""
    line_end = _line_end(text, we)
    if line_end is None:
        return None
    nxt = _line_end(text, line_end + 1) if line_end + 1 < len(text) else None
    for lo, hi in ((we, line_end), (line_end + 1, nxt)):
        if hi is None or lo >= len(text):
            break
        w0 = max(lo, hi - OPENER_TAIL - OPENER_MAX)        # an opener must end within OPENER_TAIL of the line end
        if any(hi - (w0 + m.end()) <= OPENER_TAIL for m in _OPENER.finditer(text[w0:hi])):
            hit = _first_token_after(text, hi)
            if hit:
                return hit
    return None


def _whole_word(text: str, s: int, e: int) -> bool:
    """The credential word is a whole identifier component: not inside another word (authoritative, tokens,
    tokenizer), but `_`, `-`, `.`, non-letters and camelCase boundaries count as edges (SERVICE_TOKEN, signingSecret)."""
    before = text[s - 1] if s > 0 else ""
    after = text[e] if e < len(text) else ""
    start_ok = not before.isalnum() or (before.islower() and text[s].isupper())
    end_ok = not after.isalpha() or after.isupper()
    return start_ok and end_ok


def _field_end(seg: str) -> int:
    """Where the statement's current field ends inside `seg`: a comma followed by a quote, or a brace (JSON-like
    records are one line; the value must be in the credential word's own field)."""
    for i, c in enumerate(seg):
        if c in "{}":
            return i
        if c == ",":
            j = i + 1
            while j < len(seg) and seg[j] == " ":
                j += 1
            if j < len(seg) and seg[j] in "\"'":
                return i
    return len(seg)


def proximity_findings(text: str) -> list[tuple[str, int, int]]:
    """(RULE_ID, start, end) for each credential value found by proximity (spans in `text`)."""
    found = []
    for w in WORD.finditer(text):
        we = w.end()
        if not _whole_word(text, w.start(), we):
            continue
        hit = _same_statement(text, we) or _after_opener(text, we)
        if hit:
            found.append((RULE_ID, *hit))
    return found
