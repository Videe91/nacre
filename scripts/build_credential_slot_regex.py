"""
Build the regex of the Nacre rule `credential-slot-value` (D-0011 amendment 8) from named, documented parts, so the
rule in src/nacre/ledger/data/nacre-rules-v1.toml is reproducible and reviewable. Not product code.

    .venv/bin/python scripts/build_credential_slot_regex.py          # prints the regex

Shape: KEY  SEP  VALUE  TERM, where the VALUE group is the secret.
  KEY    an identifier naming a credential, with only credential-style suffixes (so `password_length` is not a slot).
  SEP    structural syntax only: assignment / colon / space, an XML tag, a quoted key-value pair on one line, an
         optional call wrapper or auth scheme, OR a multi-line string literal whose content starts on a later line.
  VALUE  16-128 chars of [A-Za-z0-9+/=~-] containing a digit (A-0044: letters-only values are not taken).
  TERM   a quote, whitespace, ; , < ) ] } or end.

Multi-line string openers (TQ = three double quotes, TSQ = three single quotes), each from the language's own reference (owner, 2026-10-02: derive from the docs, not
from one holdout's shape):
  - Python: string prefixes (r u f b, combinations) + triple quotes        docs.python.org/3/reference/lexical_analysis.html#string-and-bytes-literals
  - Kotlin raw strings TQ                                                  kotlinlang.org/docs/strings.html#multiline-strings
  - Java text blocks TQ (content starts on the next line)                 JLS 21 §3.10.6
  - Scala TQ with an optional interpolator (s, f, raw, custom)             docs.scala-lang.org/scala3/reference (string interpolation)
  - Swift multi-line TQ and extended delimiters #"…"#, #TQ…TQ#          docs.swift.org/swift-book/…/stringsandcharacters
  - Elixir heredocs TQ and TSQ and sigil heredocs (~STQ, ~sTQ)          hexdocs.pm/elixir/syntax-reference.html, sigils
  - Ruby heredocs <<ID, <<-ID, <<~ID, quoted identifiers, method chain     docs.ruby-lang.org/en/master/syntax/literals_rdoc.html#label-Here+Documents
  - Bash here-documents <<EOF, <<-EOF, <<'EOF' (also inside $(cat …))      Bash Reference Manual §3.6.6
  - HCL / Terraform heredocs <<EOT, <<-EOT                                 developer.hashicorp.com/terraform/language/expressions/strings#heredoc-strings
  - PHP heredoc <<<ID, <<<"ID" and nowdoc <<<'ID'                          php.net/manual/en/language.types.string.php
  - PowerShell here-strings @" and @'                                      learn.microsoft.com/powershell/…/about_quoting_rules
  - C# raw string literals TQ (3+ quotes, optional $ prefixes), verbatim @"   learn.microsoft.com/dotnet/csharp/language-reference/tokens/raw-string
  - Rust raw strings r"…", r#"…"#, byte raw br#"…"#                         doc.rust-lang.org/reference/tokens.html#raw-string-literals
  - C++ raw strings R"delim( … )delim"                                     en.cppreference.com/w/cpp/language/string_literal
  - Go raw strings and JS/TS template literals (backquote)                 go.dev/ref/spec#String_literals; MDN template literals
  - TOML multi-line basic TQ and literal TSQ                              toml.io/en/v1.0.0#string
  - Dart triple quotes, raw rTSQ                                           dart.dev/language/built-in-types#strings
  - Lua long brackets [[ and [==[                                          lua.org/manual/5.4/manual.html#3.1
  - Nix indented strings ''                                                nixos.org/manual/nix/stable/language/values#type-string
  - YAML block scalars | and > with chomping (+ -) and indentation (1-9) indicators, either order   yaml.org/spec/1.2.2 §8.1.1
  - XML CDATA sections <![CDATA[ inside a credential-named element         W3C XML 1.0 §2.7
"""

KW = (r"(?:password|passwd|pwd|passphrase|secret|token|api[_\-]?key|apikey|access[_\-]?key|private[_\-]?key"
      r"|authorization|auth|credentials?|cred)")
SUFFIX = r"(?:[_\-]?(?:key|value|token|secret|str|string|b64|base64|hex|raw|plain|pass|password))*"
KEY = r"(?i:[A-Za-z0-9_.\-]*" + KW + SUFFIX + r")"

ASSIGN = r"[\"'`]?\s*(?:=|:=|:|=>|->)\s*"
WRAP = r"(?:[A-Za-z_][A-Za-z0-9_:.]*\(\s*|\$\(\s*[a-z]+\s+)?"        # call wrapper, or $(cmd …) substitution
STRPREFIX_Q = r"(?:[brfuBRFU]{0,2}[\"'`])?"                           # a string prefix only WITH its quote
SCHEME = r"(?:(?i:bearer|token|basic|bot)\s+)?"

OPENERS = "|".join([
    r"[A-Za-z~$]{0,4}(?:\"{3,}|'{3})",              # Python/Kotlin/Java/Scala/Swift/Elixir/C#/TOML/Dart triple quotes
    r"#+\"{1,3}",                                    # Swift extended delimiters
    r"[rRbB]{1,2}#*\"",                              # Rust raw strings
    r"R\"[^\s()\\\\]{0,16}\(",                       # C++ raw strings
    r"`",                                            # Go raw string, JS/TS template literal
    r"@[\"']",                                       # PowerShell here-string, C# verbatim string
    r"<<[~\-]?[\"'`]?[A-Za-z_][A-Za-z0-9_]*[\"'`]?(?:\.[A-Za-z_]+(?:\(\))?)*",   # Ruby / Bash / HCL heredocs
    r"<<<\s*[\"']?[A-Za-z_][A-Za-z0-9_]*[\"']?",     # PHP heredoc / nowdoc
    r"\[=*\[",                                       # Lua long brackets
    r"''",                                           # Nix indented strings
    r"[\"']",                                        # an ordinary quote whose content starts on the next line
])
MULTILINE = ASSIGN + WRAP + r"(?:" + OPENERS + r")[ \t]*\r?\n\s*"
YAML_BLOCK = ASSIGN + r"[>|](?:[1-9][+\-]?|[+\-][1-9]?)?[ \t]*\r?\n\s*"
XML = r">\s*(?:<!\[CDATA\[\s*)?"

SEP = "(?:" + "|".join([
    MULTILINE,
    YAML_BLOCK,
    ASSIGN + WRAP + STRPREFIX_Q + SCHEME,            # single-line assignment
    r"[\"'][ \t]*,[ \t]*[\"']",                     # ('key', 'value') on one line
    XML,
    r"\s+" + r"[\"'`]?" + SCHEME,                   # key value / --key value / header value
]) + ")"

C, L = r"[A-Za-z0-9+/=\-~]", r"[A-Za-z+/=\-~]"
VALUE = "(" + "|".join([f"{L}{{{p}}}[0-9]{C}{{{15 - p},{127 - p}}}" for p in range(16)]
                       + [f"{L}{{16,127}}[0-9]{C}{{0,111}}"]) + ")"
TERM = r"(?:[\"'`<\s;,)\]}]|$)"

REGEX = KEY + SEP + VALUE + TERM

if __name__ == "__main__":
    import re2
    re2.compile(REGEX)
    assert "'''" not in REGEX                        # it is stored in a TOML literal string
    print(REGEX)
