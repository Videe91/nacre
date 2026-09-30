"""
Secret-detection corpus framework (D-0007 amendments 2-4). Test code only; never imported by src/.

Positives are GENERATED at test time from seeded generators. Only generators and a sha256 of their
output are committed, never a secret-shaped string. Each generator states its format source
(provider docs or a standard) and is written independently of the gitleaks rules.

Sample.expected is one of:
  "secret"   must be stripped (counts toward its provider's / generic kind's catch rate);
  "public"   public-by-design credential: neutral, excluded from both rates, reported separately;
  "negative" no secret: any redaction inside it is a false positive.
"Caught" = after stripping, `secret` no longer occurs anywhere in the text.
"""
import hashlib
import json
import random
from collections.abc import Callable
from dataclasses import asdict, dataclass

GENERATORS: dict[str, "Generator"] = {}


@dataclass(frozen=True)
class Sample:
    category: str        # "provider" | "generic" | "negative"
    provider: str        # provider name, or the generic kind, or the negative source
    kind: str            # token type
    expected: str        # "secret" | "public" | "negative"
    text: str            # the full document the scanner sees
    secret: str | None   # the exact string that must disappear (None for negatives)


@dataclass(frozen=True)
class Generator:
    name: str
    category: str
    provider: str
    kind: str
    expected: str
    source: str          # URL(s) or standard the format was verified against
    make: Callable[[random.Random], str]   # returns the bare credential (or a whole document for multi-line ones)
    embed: bool = True   # False: `make` returns a complete document (e.g. a PEM file)
    count: int | None = None   # overrides per_generator (e.g. RSA key generation is slow)


def generator(*, category, provider, kind, source, expected="secret", embed=True, count=None):
    """Register a generator. Its seed depends only on its own name and the corpus seed."""
    def register(make):
        name = f"{category}/{provider}/{kind}"
        if name in GENERATORS:
            raise ValueError(f"duplicate generator {name}")
        GENERATORS[name] = Generator(name, category, provider, kind, expected, source, make, embed, count)
        return make
    return register


# Contexts a coding agent's event might carry a credential in. The variable name is neutral on purpose,
# so detection cannot lean on a suggestive key name.
CONTEXTS = [
    lambda v: f"VALUE={v}\n",
    lambda v: f'{{"value": "{v}"}}\n',
    lambda v: f'value = "{v}"\n',
    lambda v: f"export VALUE='{v}'\n",
    lambda v: f"value: {v}\n",
    lambda v: f'curl -H "Authorization: Bearer {v}" https://api.example.com/v1/items\n',
    lambda v: f"2026-09-30T12:00:00Z INFO client configured with {v}\n",
]


# SEALED HOLDOUT CONTEXTS (D-0011 amendment 1). Committed before any Nacre rule was written. Never
# consult holdout samples or holdout results while writing or tuning rules; official rates come from here.
HOLDOUT_SEED = 77_031_117
HOLDOUT_CONTEXTS = [
    lambda v: f'[service]\ncredential = "{v}"\n',
    lambda v: f"<config><credential>{v}</credential></config>\n",
    lambda v: f"Use this for now:\n```\n{v}\n```\n",
    lambda v: f'client := api.NewClient("{v}")\n',
    lambda v: f"const client = new Client({{ auth: '{v}' }});\n",
    lambda v: f"X-Api-Key: {v}\n",
    lambda v: f"INSERT INTO settings (k, v) VALUES ('cred', '{v}');\n",
    lambda v: f"tool login --token={v} --verbose\n",
    lambda v: f"error: authentication failed for {v}: 401 Unauthorized\n",
]


def _rng(seed: int, name: str) -> random.Random:
    return random.Random(int.from_bytes(hashlib.sha256(f"{seed}|{name}".encode()).digest()[:8], "big"))


def build(seed: int = 20260930, per_generator: int = 50, contexts=None,
          embed_documents: bool = False, cover_all_contexts: bool = False,
          credential_slots=None, embed_negatives: bool = True) -> list[Sample]:
    """Every registered generator, `per_generator` samples each, deterministic for a given seed.
    Default = the WORKING set. embed_documents: multi-line documents (PEM, .env) are embedded in the
    contexts too. cover_all_contexts: every generator yields at least one sample per context
    (D-0011 amendment 5: every covered rule in every embedding context).
    D-0011 amendment 8 (opt-in, so earlier corpora rebuild byte-identically):
      credential_slots: indices of `contexts` that name a credential. When given, the
        "credential-slot" generators run, cycling ONLY over those contexts; when None they are skipped.
      embed_negatives=False: synthetic negatives are never embedded (so never in a credential slot)."""
    contexts = contexts or CONTEXTS
    samples = []
    for name in sorted(GENERATORS):
        g, rng = GENERATORS[name], _rng(seed, name)
        pool = contexts
        if g.category == "credential-slot":
            if credential_slots is None:
                continue
            pool = [contexts[i] for i in sorted(credential_slots)]
        n = g.count or per_generator
        if cover_all_contexts:
            n = max(n, len(pool))
        embed = g.embed or (embed_documents and (g.expected != "negative" or embed_negatives))
        for i in range(n):
            value = g.make(rng)
            text = pool[i % len(pool)](value) if embed else value
            samples.append(Sample(g.category, g.provider, g.kind, g.expected, text,
                                  None if g.expected == "negative" else _secret_of(value, g)))
    return samples


# Credential-slot contexts (D-0011 amendment 8): indices of contexts that name a credential.
WORKING_CREDENTIAL_SLOTS = frozenset({5})                  # curl Authorization: Bearer
H1_CREDENTIAL_SLOTS = frozenset({0, 1, 4, 5, 6, 7})       # credential=, <credential>, auth:, X-Api-Key, 'cred', --token=


def build_working() -> list[Sample]:
    """Working data under D-0011 amendment 8: the working set + demoted H1, credential slots labelled,
    synthetic negatives never embedded."""
    return (build(credential_slots=WORKING_CREDENTIAL_SLOTS, embed_negatives=False)
            + build(seed=HOLDOUT_SEED, contexts=HOLDOUT_CONTEXTS, credential_slots=H1_CREDENTIAL_SLOTS,
                    embed_negatives=False))


def build_holdout(per_generator: int = 50) -> list[Sample]:
    """The sealed holdout: other seed, other contexts. Official catch rates come from here only."""
    return build(seed=HOLDOUT_SEED, per_generator=per_generator, contexts=HOLDOUT_CONTEXTS)


def secret_parts(sample: Sample) -> list[tuple[int, int]]:
    """Spans, within sample.secret, that must be fully redacted for "caught" (D-0011 amendment 8).
    Documented public prefixes and format markers may remain. Default: the whole secret."""
    g = GENERATORS[f"{sample.category}/{sample.provider}/{sample.kind}"]
    parts = getattr(g.make, "parts_of", None)
    return parts(sample.secret) if parts else [(0, len(sample.secret))]


def after_prefix(prefix: str):
    """parts_of helper: everything after a documented public prefix."""
    def parts(secret: str):
        assert secret.startswith(prefix), prefix
        return [(len(prefix), len(secret))]
    return parts


def _secret_of(value: str, g: Generator) -> str:
    # For embedded credentials the whole value is the secret; document generators return
    # (document) and mark their secret via a registered extractor on the function, if any.
    extract = getattr(g.make, "secret_of", None)
    return extract(value) if extract else value


def digest(samples: list[Sample]) -> str:
    """sha256 of the corpus, pinned in the tests so any change to a generator is deliberate."""
    blob = json.dumps([asdict(s) for s in samples], sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(blob).hexdigest()


# ---- small helpers shared by generators ---------------------------------------------------------
BASE62 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
ALNUM = BASE62
HEX = "0123456789abcdef"
B64URL = ALNUM + "-_"


def rand(rng: random.Random, alphabet: str, n: int) -> str:
    return "".join(rng.choice(alphabet) for _ in range(n))
