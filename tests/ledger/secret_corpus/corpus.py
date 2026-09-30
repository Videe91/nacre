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


def _rng(seed: int, name: str) -> random.Random:
    return random.Random(int.from_bytes(hashlib.sha256(f"{seed}|{name}".encode()).digest()[:8], "big"))


def build(seed: int = 20260930, per_generator: int = 50) -> list[Sample]:
    """Every registered generator, `per_generator` samples each, deterministic for a given seed."""
    samples = []
    for name in sorted(GENERATORS):
        g, rng = GENERATORS[name], _rng(seed, name)
        for i in range(g.count or per_generator):
            value = g.make(rng)
            text = CONTEXTS[i % len(CONTEXTS)](value) if g.embed else value
            samples.append(Sample(g.category, g.provider, g.kind, g.expected, text,
                                  None if g.expected == "negative" else _secret_of(value, g)))
    return samples


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
