"""
Functionality: Find secrets in text and replace them with redaction markers before anything is written.
Owns: loading and pinning the vendored gitleaks rules, running them under RE2 with gitleaks' semantics,
  the public-credential layer, the entropy layer, merging overlapping findings, and the redaction.
Public entry: strip_secrets(), StripResult, Finding
Decisions: D-0002, D-0007, D-0008, D-0009, D-0011
Assumptions: A-0010, A-0018
Notes: Layers, in order:
  1. Public credentials (D-0007 amendment 4): public-by-design values are recognised, NOT stripped, and
     reported as kinds (D-0008 amendment 5). A secret finding overlapping a public span is dropped.
     Look-alikes are told apart structurally, e.g. a Supabase JWT is public only if its role claim is "anon".
  2. gitleaks v8.30.1 rules under google-re2 (D-0009), with keywords prefilter, secretGroup / first
     non-empty group, per-rule entropy (drop if <= threshold), global and per-rule allowlists (paths
     only when a path is given; regexTarget secret|match|line; stopwords; condition OR|AND).
     Then the Nacre supplementary rules (D-0011), same semantics, additive only: duplicate ids are
     refused, gitleaks' global allowlist does not apply to them, their own allowlists apply only to them.
  3. Entropy layer (D-0007): a high-entropy token right after an assignment or credential cue. D1
     parameters, tuned on the corpus: see ENTROPY_* below. It skips runs longer than 64 (blobs,
     hashes), runs starting "//" (URL tails), and unquoted values after a spaced " = " (code
     expressions such as `X = module.Name`), and requires lower, upper and digits.
  Only the secret part of a match is replaced, as "[REDACTED:<rule id>]", so the surrounding context
  (e.g. "token = ") survives. Overlapping secret spans are merged; the label is the most specific rule
  (provider rule > generic rule > entropy).
  The rules file is checked against its pinned sha256 before use, and any rule that fails to compile
  fails the load: a rule is never silently skipped (D-0009).
"""
import base64
import binascii
import hashlib
import json
import math
import tomllib
from collections import Counter
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import re2

RULES_FILE = Path(__file__).resolve().parent / "data" / "gitleaks-v8.30.1.toml"
RULES_SHA256 = "e163e53b9e7e8a8511e77271e2b323ed057759542a6d988258afe3a1fa329caf"
NACRE_RULES_FILE = Path(__file__).resolve().parent / "data" / "nacre-rules-v1.toml"
NACRE_RULES_SHA256 = "117853c0909443eace0dcbd85fa52f140d61df11b51ce34ff6b83b1d00f5adcb"

ENTROPY_MIN_BITS = 4.3        # above hex's 4.0 ceiling
ENTROPY_MIN_LEN, ENTROPY_MAX_LEN = 20, 64   # longer runs are data (blobs, hashes), not credentials
ENTROPY_CANDIDATE = re2.compile(
    r"""(?i)(?:[:=]|=>|\bBearer|\btoken|\bsecret|\bpassword|\bpasswd|\bapi[_-]?key)["'\s]{0,3}([A-Za-z0-9+/=_\-.~]{20,})""")
ENTROPY_NOT_AFTER = ("sha1-", "sha256-", "sha384-", "sha512-", "sha256:", "base64,", "integrity")


@dataclass(frozen=True, slots=True)
class Finding:
    rule_id: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class StripResult:
    text: str
    redactions: tuple[str, ...]           # rule ids, in order of appearance (D-0008 body `redactions`)
    public_credentials: tuple[str, ...]   # public kinds found, sorted, unique (D-0008 amendment 5)
    findings: tuple[Finding, ...]         # secret spans in the ORIGINAL text, merged


class RulesError(RuntimeError):
    """The vendored rules file is not the pinned one, or a rule does not compile."""


def strip_secrets(text: str, path: str | None = None) -> StripResult:
    """Redact every secret in `text`; leave public credentials in place and report their kinds."""
    public = _public_spans(text)
    found = [f for f in _rule_findings(text, path) + _entropy_findings(text)
             if not any(f.start < e and s < f.end for s, e, _ in public)]
    merged = _merge(found)
    out, last = [], 0
    for f in merged:
        out.append(text[last:f.start])
        out.append(f"[REDACTED:{f.rule_id}]")
        last = f.end
    out.append(text[last:])
    return StripResult("".join(out), tuple(f.rule_id for f in merged),
                       tuple(sorted({kind for _, _, kind in public})), tuple(merged))


# ---- layer 1: public credentials ----------------------------------------------------------------
_JWT = re2.compile(r"\beyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
_PUBLIC = [
    ("stripe-publishable", re2.compile(r"\bpk_(?:live|test)_[A-Za-z0-9]+")),
    ("supabase-publishable", re2.compile(r"\bsb_publishable_[A-Za-z0-9_-]{22}_[A-Za-z0-9_-]{8}")),
    ("sentry-dsn-public", re2.compile(r"\bhttps?://[0-9a-f]{32}@[A-Za-z0-9.-]+(?::[0-9]+)?(?:/[A-Za-z0-9._-]+)*/[0-9]+\b")),
]


def _public_spans(text):
    spans = [(m.start(), m.end(), kind) for kind, rx in _PUBLIC for m in rx.finditer(text)]
    for m in _JWT.finditer(text):
        if _jwt_claim(m.group(0), "role") == "anon":      # Supabase anon key; service_role stays secret
            spans.append((m.start(), m.end(), "supabase-anon-jwt"))
    return spans


def _jwt_claim(token, claim):
    try:
        payload = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))).get(claim)
    except (ValueError, binascii.Error, AttributeError, IndexError):
        return None


# ---- layer 2: gitleaks rules ----------------------------------------------------------------------
def _load(path, pinned, origin):
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != pinned:
        raise RulesError(f"{path.name} does not match its pinned sha256 (D-0007, D-0011)")
    cfg = tomllib.loads(raw.decode())
    rules = []
    try:
        for r in cfg["rules"]:
            if "regex" not in r:
                continue                                   # path-only rules do not apply to payloads
            allows = [(a.get("condition", "OR").upper(), a.get("regexTarget", "secret"),
                       [re2.compile(p) for p in a.get("paths", [])], [re2.compile(x) for x in a.get("regexes", [])],
                       [w.lower() for w in a.get("stopwords", [])]) for a in r.get("allowlists", [])]
            rules.append((r["id"], re2.compile(r["regex"]), [k.lower() for k in r.get("keywords", [])],
                          r.get("entropy"), r.get("secretGroup", 0), allows, origin))
        glob = cfg.get("allowlist", {})
        global_allow = ([re2.compile(p) for p in glob.get("paths", [])],
                        [re2.compile(x) for x in glob.get("regexes", [])],
                        [w.lower() for w in glob.get("stopwords", [])])
    except re2.error as exc:
        raise RulesError(f"a rule in {path.name} failed to compile under re2: {exc}") from None
    return global_allow, rules


@cache
def _rules():
    global_allow, vendored = _load(RULES_FILE, RULES_SHA256, "gitleaks")
    _, nacre = _load(NACRE_RULES_FILE, NACRE_RULES_SHA256, "nacre")
    clash = {r[0] for r in vendored} & {r[0] for r in nacre}
    if clash:
        raise RulesError(f"Nacre rules may not reuse gitleaks rule ids (additive only, D-0011): {sorted(clash)}")
    return global_allow, vendored + nacre


def _rule_findings(text, path):
    (g_paths, g_regexes, g_stopwords), rules = _rules()
    skip_vendored = path is not None and any(p.search(path) for p in g_paths)
    lowered, found = text.lower(), []
    for rule_id, rx, keywords, min_entropy, group, allows, origin in rules:
        if (origin == "gitleaks" and skip_vendored) or (keywords and not any(k in lowered for k in keywords)):
            continue
        for m in rx.finditer(text):
            idx = group or next((i for i in range(1, len(m.groups()) + 1) if m.group(i)), 0)
            if idx > len(m.groups()):
                continue
            start, end = m.span(idx)
            secret = text[start:end]
            if not secret or (min_entropy and _entropy(secret) <= min_entropy):
                continue
            if origin == "gitleaks" and (any(x.search(secret) for x in g_regexes)
                                         or any(w in secret.lower() for w in g_stopwords)):
                continue
            if any(_allowed(a, path, secret, m.group(0), _line(text, m.start(), m.end())) for a in allows):
                continue
            found.append(Finding(rule_id, start, end))
    return found


def _allowed(allow, path, secret, match, line):
    condition, target, paths, regexes, stopwords = allow
    subject = {"match": match, "line": line}.get(target, secret)
    checks = []
    if paths:
        checks.append(path is not None and any(p.search(path) for p in paths))
    if regexes:
        checks.append(any(x.search(subject) for x in regexes))
    if stopwords:
        checks.append(any(w in secret.lower() for w in stopwords))
    return bool(checks) and (all(checks) if condition == "AND" else any(checks))


def _line(text, start, end):
    a = text.rfind("\n", 0, start) + 1
    b = text.find("\n", end)
    return text[a:b if b != -1 else len(text)]


# ---- layer 3: entropy ---------------------------------------------------------------------------------
def _entropy(s):
    counts, n = Counter(s), len(s)
    return -sum(c / n * math.log2(c / n) for c in counts.values()) if n else 0.0


def _entropy_findings(text):
    found = []
    for m in ENTROPY_CANDIDATE.finditer(text):
        start, end = m.span(1)
        token = text[start:end]
        before = text[max(0, start - 16):start].lower()
        if token.startswith("//"):
            continue                                        # the rest of a URL after "scheme:"
        spaced_assign = text[m.start()] == "=" and m.start() > 0 and text[m.start() - 1] == " "
        if spaced_assign and text[start - 1] not in "'\"":
            continue                                        # code-style `x = expr`: only string literals count
        classes = sum(any(c in s for c in token) for s in ("abcdefghijklmnopqrstuvwxyz",
                                                           "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "0123456789"))
        if (ENTROPY_MIN_LEN <= len(token) <= ENTROPY_MAX_LEN and classes == 3
                and _entropy(token) >= ENTROPY_MIN_BITS and not any(x in before for x in ENTROPY_NOT_AFTER)):
            found.append(Finding("entropy", start, end))
    return found


# ---- merge -----------------------------------------------------------------------------------------
def _label_rank(rule_id):
    """Which rule names a merged span: provider-specific first, then generic rules, then entropy (D1).
    Labels only; merging never changes what is redacted."""
    if rule_id == "entropy":
        return 2
    return 1 if rule_id.startswith("generic-") or rule_id == "url-userinfo-password" else 0


def _merge(found):
    clusters = []
    for f in sorted(found, key=lambda f: (f.start, -f.end)):
        if clusters and f.start < clusters[-1][1]:
            clusters[-1][1] = max(clusters[-1][1], f.end)
            clusters[-1][2].append(f.rule_id)
        else:
            clusters.append([f.start, f.end, [f.rule_id]])
    return [Finding(min(ids, key=_label_rank), start, end) for start, end, ids in clusters]
