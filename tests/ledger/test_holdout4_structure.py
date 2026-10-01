"""Structure-only checks of sealed holdout H4 (D-0011 amendment 8). Never calls a detector or a measurement.

Checks: context count and slot labelling, disjointness from every earlier context, each value embedded
exactly once and unmodified in the context the build assigns, credential-slot values only in slot
contexts, synthetic negatives raw (never embedded, so never in a slot), the real-code negatives (>= 150: stdlib plus a
licensed third-party top-up) matching their manifest, disjoint by path and content sha256 from negatives/,
negatives_external/, H2, H3 and the withdrawn first draw, every file under an approved licence with its licence file,
and the HOLDOUT4_MANIFEST.json hashes."""
import hashlib
import json
from collections import defaultdict
from pathlib import Path

import pytest

from secret_corpus import holdout_2, holdout_3, holdout_4
from secret_corpus.corpus import CONTEXTS, GENERATORS, HOLDOUT_CONTEXTS, build, digest

pytestmark = pytest.mark.holdout

CORPUS_DIR = Path(__file__).resolve().parent / "secret_corpus"
MANIFEST = json.loads((CORPUS_DIR / "HOLDOUT4_MANIFEST.json").read_text())
NEG_DIR = CORPUS_DIR / "negatives_holdout4"
PROBE = "PROBE-VALUE-123"
CTX = holdout_4.HOLDOUT4_CONTEXTS
SLOTS = holdout_4.HOLDOUT4_CREDENTIAL_SLOTS
EARLIER = CONTEXTS + HOLDOUT_CONTEXTS + holdout_2.HOLDOUT2_CONTEXTS + holdout_3.HOLDOUT3_CONTEXTS


@pytest.fixture(scope="module")
def h4():
    return holdout_4.build_holdout4()


@pytest.fixture(scope="module")
def raw():
    """The same build with identity contexts: contexts draw no randomness, so this yields the bare values
    (or documents) in the same order. Used to prove each H4 text is exactly context(value)."""
    return build(seed=holdout_4.HOLDOUT4_SEED, per_generator=50, contexts=[lambda v: v] * len(CTX),
                 embed_documents=True, cover_all_contexts=True, credential_slots=SLOTS, embed_negatives=False)


def test_context_count_and_slot_labelling():
    assert len(CTX) == 17
    assert SLOTS == frozenset(range(9))
    assert 9 <= len(SLOTS) <= len(CTX) - 5
    assert MANIFEST["contexts"]["count"] == len(CTX)
    assert MANIFEST["contexts"]["credential_slots"] == sorted(SLOTS)


def _window(text):
    """The probe's line with its neighbours: the local context a detector sees around the value."""
    lines = text.splitlines()
    (j,) = [n for n, ln in enumerate(lines) if PROBE in ln]
    return tuple(lines[max(j - 1, 0):j + 2])


def test_contexts_disjoint_from_every_earlier_holdout():
    earlier_full = {c(PROBE) for c in EARLIER}
    earlier_windows = {_window(c(PROBE)) for c in EARLIER}
    for i, c in enumerate(CTX):
        text = c(PROBE)
        assert text.count(PROBE) == 1, i
        assert text not in earlier_full, i
        assert _window(text) not in earlier_windows, i
    assert len({c(PROBE) for c in CTX}) == len(CTX)


def test_each_value_embedded_once_unmodified_in_its_assigned_context(h4, raw):
    assert len(h4) == len(raw)
    seen = defaultdict(int)
    covered = defaultdict(set)
    for s, r in zip(h4, raw, strict=True):
        name = f"{s.category}/{s.provider}/{s.kind}"
        assert (s.category, s.provider, s.kind, s.expected, s.secret) == (r.category, r.provider, r.kind,
                                                                         r.expected, r.secret)
        k = seen[name]
        seen[name] += 1
        if s.expected == "negative":
            assert s.secret is None and s.text == r.text, name   # raw, never embedded
            continue
        pool = sorted(SLOTS) if s.category == "credential-slot" else list(range(len(CTX)))
        i = pool[k % len(pool)]
        covered[name].add(i)
        assert s.text == CTX[i](r.text), name                 # unmodified value/document in context i
        assert s.text.count(r.text) == 1, name                # the value (or document) appears once
        assert s.text.count(s.secret) == 1, name              # and its secret once
    for name, idx in covered.items():
        want = set(SLOTS) if GENERATORS[name].category == "credential-slot" else set(range(len(CTX)))
        assert idx == want, name


def test_credential_slot_values_only_in_slot_contexts_and_negatives_in_none(h4):
    affixes = [tuple(c(PROBE).split(PROBE)) for c in CTX]

    def contexts_of(text):
        return {i for i, (pre, suf) in enumerate(affixes)
                if text.startswith(pre) and text.endswith(suf) and len(text) >= len(pre) + len(suf)}

    slot_samples = [s for s in h4 if s.category == "credential-slot"]
    assert slot_samples
    for s in slot_samples:
        assert len(contexts_of(s.text)) == 1 and contexts_of(s.text) <= SLOTS
    negs = [s for s in h4 if s.expected == "negative"]
    assert negs
    for s in negs:
        assert s.secret is None and not contexts_of(s.text)


APPROVED_LICENCES = {"MIT", "BSD-2-Clause", "BSD-3-Clause", "Apache-2.0", "PSF-2.0", "MIT OR Apache-2.0"}
EARLIER_NEG_DIRS = ("negatives", "negatives_external", "negatives_holdout2", "negatives_holdout3")


@pytest.fixture(scope="module")
def neg():
    return json.loads((NEG_DIR / "MANIFEST.json").read_text())


def test_negatives_match_manifest(neg):
    on_disk = {p.relative_to(NEG_DIR).as_posix() for p in NEG_DIR.rglob("*")
               if p.is_file() and p.name != "MANIFEST.json" and "/LICENSES/" not in p.as_posix()}
    paths = {f["path"] for f in neg["files"]}
    assert on_disk == paths and len(paths) == len(neg["files"]) >= 150
    assert len({f["sha256"] for f in neg["files"]}) == len(neg["files"])
    for f in neg["files"]:
        data = (NEG_DIR / f["path"]).read_bytes()
        assert data and len(data) == f["bytes"] <= 16384 and hashlib.sha256(data).hexdigest() == f["sha256"], f
    c = neg["composition"]
    assert c["total"] == len(neg["files"]) == c["stdlib"] + c["third_party"]
    assert sum(f["origin"].startswith("stdlib") for f in neg["files"]) == c["stdlib"]
    assert [{"path": f["path"], "sha256": f["sha256"]} for f in neg["files"]] == MANIFEST["negatives"]["files"]
    assert MANIFEST["negatives"]["count"] == len(neg["files"])


def test_negatives_disjoint_from_every_earlier_set_and_the_first_draw(neg):
    paths = {f["path"] for f in neg["files"]}
    hashes = {f["sha256"] for f in neg["files"]}
    for sub in EARLIER_NEG_DIRS:
        earlier = json.loads((CORPUS_DIR / sub / "MANIFEST.json").read_text())["files"]
        assert not paths & {f["path"] for f in earlier}, sub
        assert not hashes & {f["sha256"] for f in earlier}, sub
    first = json.loads((CORPUS_DIR / "HOLDOUT4_FIRST_DRAW_NEGATIVES.json").read_text())
    withdrawn = MANIFEST["negatives"]["withdrawn_first_draw"]
    assert withdrawn["seed"] == first["seed"] and withdrawn["manifest_sha256"] == first["manifest_sha256"]
    assert [f["path"] for f in withdrawn["files"]] == first["paths"]
    assert not paths & set(first["paths"])
    assert not hashes & {f["sha256"] for f in withdrawn["files"]}


def test_every_negative_has_an_approved_licence_and_licence_file(neg):
    lic = NEG_DIR / "LICENSES"
    assert (lic / "LICENSE-PSF.txt").is_file() and neg["stdlib_licence"] == "PSF-2.0"
    pkgs = neg["packages"]
    for f in neg["files"]:
        if f["origin"].startswith("stdlib"):
            assert f["licence"] == "PSF-2.0", f["path"]
            continue
        dist, version = f["origin"].split(" ")
        p = pkgs[dist]
        assert p["version"] == version and f["licence"] == p["licence"] in APPROVED_LICENCES, f["path"]
        assert f["path"].startswith(f"{dist}-{version}/"), f["path"]
        assert "test" not in f["path"].lower() and "vendor" not in f["path"].lower(), f["path"]
    for dist, p in pkgs.items():
        assert p["licence_files"] and all((NEG_DIR / n).is_file() and n.startswith(f"LICENSES/{dist}-{p['version']}-")
                                          for n in p["licence_files"]), dist
        assert p["count"] == sum(f["origin"] == f"{dist} {p['version']}" for f in neg["files"]) >= 1, dist
    assert sum(p["count"] for p in pkgs.values()) == neg["composition"]["third_party"]
    listed = {n for p in pkgs.values() for n in p["licence_files"]} | {"LICENSES/LICENSE-PSF.txt"}
    assert {p.relative_to(NEG_DIR).as_posix() for p in lic.iterdir()} == listed


def test_manifest_hashes(h4):
    sha = MANIFEST["sha256"]
    assert MANIFEST["seed"] == holdout_4.HOLDOUT4_SEED
    assert digest(h4) == sha["corpus_digest"]
    assert hashlib.sha256((CORPUS_DIR / "holdout_4.py").read_bytes()).hexdigest() == sha["holdout_4_py_sha256"]
    assert hashlib.sha256((NEG_DIR / "MANIFEST.json").read_bytes()).hexdigest() == sha["negatives_manifest_sha256"]
    parts = {k: sha[k] for k in ("corpus_digest", "holdout_4_py_sha256", "negatives_manifest_sha256")}
    assert hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest() == sha["total"]
