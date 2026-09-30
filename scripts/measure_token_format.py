"""
Measure the FORMAT of real tokens you own, without revealing them (D-0007 amendment 4).
Run it yourself, locally. Real tokens must never enter the repo, chat or an agent's context.

    .venv/bin/python scripts/measure_token_format.py

You type the provider, the token type and the prefix you EXPECT (public knowledge, e.g. "sk_live_").
Then paste each token at a hidden prompt (no echo). The script prints only:
  - whether every token starts with the prefix you typed,
  - total length (min-max over samples),
  - the character classes after the prefix (a-z, A-Z, 0-9, and which punctuation characters occur),
  - lengths of dot-separated segments, if the token contains dots,
and a suggested ASSUMPTIONS.md row (sample count + date; 1 sample = provisional).

It NEVER prints characters taken from the token: the prefix shown is the one you typed, confirmed
yes/no. It writes no files, logs nothing, and keeps no history. Python cannot reliably wipe strings
from memory; close the terminal afterwards if that matters to you.
Multi-line secrets (PEM keys) are not supported; they are covered by the generic category.
"""
import getpass
import string
import sys
from datetime import date

_CLASSES = [("a-z", set(string.ascii_lowercase)), ("A-Z", set(string.ascii_uppercase)), ("0-9", set(string.digits))]


def describe(tokens: list[str], prefix: str) -> dict:
    """Format facts about `tokens`. Contains lengths, class names and punctuation only; never token text."""
    if not tokens:
        raise ValueError("no tokens")
    if any(not t or any(c.isspace() for c in t) for t in tokens):
        raise ValueError("tokens must be non-empty and contain no whitespace")
    rests = [t[len(prefix):] if t.startswith(prefix) else t for t in tokens]
    classes = set()
    punctuation = set()
    for r in rests:
        for c in r:
            for name, members in _CLASSES:
                if c in members:
                    classes.add(name)
                    break
            else:
                punctuation.add(c)
    dotted = sorted({tuple(len(seg) for seg in t.split(".")) for t in tokens if "." in t})
    return {
        "samples": len(tokens),
        "prefix": prefix,
        "prefix_matches": sum(t.startswith(prefix) for t in tokens),
        "length_min": min(map(len, tokens)),
        "length_max": max(map(len, tokens)),
        "classes": [n for n, _ in _CLASSES if n in classes],
        "punctuation": "".join(sorted(punctuation)),
        "dot_segments": [list(d) for d in dotted],
    }


def render(provider: str, token_type: str, facts: dict, today: date) -> str:
    n = facts["samples"]
    length = (str(facts["length_min"]) if facts["length_min"] == facts["length_max"]
              else f"{facts['length_min']}-{facts['length_max']}")
    charset = " ".join(facts["classes"]) + (f" plus {facts['punctuation']!r}" if facts["punctuation"] else "")
    dots = f"; dot segments {facts['dot_segments']}" if facts["dot_segments"] else ""
    status = "open (provisional: 1 sample)" if n == 1 else "open"
    return "\n".join([
        f"provider: {provider} | type: {token_type} | samples: {n}",
        f"prefix {facts['prefix']!r} matched {facts['prefix_matches']}/{n}",
        f"length: {length}",
        f"charset after prefix: {charset or '(none)'}{dots}",
        "",
        "Suggested ASSUMPTIONS.md row (fill in the A-ID):",
        f"| A-XXXX | {provider} {token_type} tokens: prefix `{facts['prefix']}` ({facts['prefix_matches']}/{n}), "
        f"length {length}, charset {charset}{dots} | Owner-measured with scripts/measure_token_format.py on "
        f"{n} real token(s), {today.isoformat()} | Re-measure with more samples; compare with official SDK "
        f"validation code | Corpus generator for this type is unrealistic; its catch rate is misleading | {status} |",
    ])


def main(read_secret=getpass.getpass, read_line=input, out=print, today=None) -> int:
    provider = read_line("Provider (e.g. Stripe): ").strip()
    token_type = read_line("Token type (e.g. secret key): ").strip()
    prefix = read_line("Expected prefix, typed by you (e.g. sk_live_; empty if none): ").strip()
    tokens = []
    while True:
        token = read_secret(f"Paste token #{len(tokens) + 1} (hidden; empty line to finish): ").strip()
        if not token:
            break
        tokens.append(token)
    if not tokens:
        out("No tokens given; nothing measured.")
        return 1
    try:
        facts = describe(tokens, prefix)
    except ValueError as exc:
        out(f"Refused: {exc}")
        return 1
    finally:
        tokens.clear()
    out(render(provider, token_type, facts, today or date.today()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
