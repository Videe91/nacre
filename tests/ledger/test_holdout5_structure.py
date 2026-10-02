"""Structure-only checks of sealed holdout H5 (D-0011 amendment 8). Never calls a detector or a measurement.

Checks: context count and slot labelling, at least 7 credential-slot contexts that are multi-line string literals
with the value on its own line, disjointness from every earlier context (working set, H1, H2, H3, H4), each value
embedded exactly once and unmodified in the context the build assigns, credential-slot values only in slot contexts,
synthetic negatives raw (never embedded, so never in a slot), the 150 real-code negatives matching their manifest,
disjoint by path and content sha256 from negatives/, negatives_external/, H2, H3, H4 and the H4 withdrawn first
draw, every file under an approved licence with its licence file and no package over 15% of the set, and the
HOLDOUT5_MANIFEST.json hashes."""
import hashlib
import json
from collections import defaultdict
from pathlib import Path

import pytest

from secret_corpus import holdout_2, holdout_3, holdout_4, holdout_5
from secret_corpus.corpus import CONTEXTS, GENERATORS, HOLDOUT_CONTEXTS, build, digest

pytestmark = pytest.mark.holdout

CORPUS_DIR = Path(__file__).resolve().parent / "secret_corpus"
MANIFEST = json.loads((CORPUS_DIR / "HOLDOUT5_MANIFEST.json").read_text())
NEG_DIR = CORPUS_DIR / "negatives_holdout5"
PROBE = "PROBE-VALUE-123"
CTX = holdout_5.HOLDOUT5_CONTEXTS
SLOTS = holdout_5.HOLDOUT5_CREDENTIAL_SLOTS
ML_SLOTS = holdout_5.HOLDOUT5_MULTILINE_SLOTS
ML_NON_SLOTS = holdout_5.HOLDOUT5_MULTILINE_NON_SLOTS
EARLIER = (CONTEXTS + HOLDOUT_CONTEXTS + holdout_2.HOLDOUT2_CONTEXTS + holdout_3.HOLDOUT3_CONTEXTS
           + holdout_4.HOLDOUT4_CONTEXTS)


@pytest.fixture(scope="module")
def h5():
    return holdout_5.build_holdout5()


@pytest.fixture(scope="module")
def raw():
    """The same build with identity contexts: contexts draw no randomness, so this yields the bare values
    (or documents) in the same order. Used to prove each H5 text is exactly context(value)."""
    return build(seed=holdout_5.HOLDOUT5_SEED, per_generator=50, contexts=[lambda v: v] * len(CTX),
                 embed_documents=True, cover_all_contexts=True, credential_slots=SLOTS, embed_negatives=False)


def test_context_count_and_slot_labelling():
    assert len(CTX) == 22
    assert SLOTS == frozenset(range(15))
    assert len(SLOTS) >= 12 and len(CTX) - len(SLOTS) >= 7
    assert MANIFEST["contexts"]["count"] == len(CTX)
    assert MANIFEST["contexts"]["credential_slots"] == sorted(SLOTS)
    assert MANIFEST["contexts"]["multiline_credential_slots"] == sorted(ML_SLOTS)
    assert MANIFEST["contexts"]["multiline_non_slots"] == sorted(ML_NON_SLOTS)
    assert len(MANIFEST["contexts"]["descriptions"]) == len(CTX)


def test_at_least_seven_multiline_literal_slots_with_the_value_on_its_own_line():
    assert len(ML_SLOTS) >= 7 and ML_SLOTS <= SLOTS and not ML_NON_SLOTS & SLOTS
    for i in ML_SLOTS | ML_NON_SLOTS:
        lines = CTX[i](PROBE).splitlines()
        (j,) = [n for n, ln in enumerate(lines) if PROBE in ln]
        assert lines[j].strip() == PROBE, i                    # alone on its line (indentation only)
        assert 0 < j < len(lines) - 1, i                       # opening delimiter above, closing below
    for i in SLOTS - ML_SLOTS:                                 # ordinary single-line slots share the line
        (line,) = [ln for ln in CTX[i](PROBE).splitlines() if PROBE in ln]
        assert line.strip() != PROBE, i
    # the multi-line slots are written in distinct languages/formats (first line of each differs)
    assert len({CTX[i](PROBE).splitlines()[0] for i in ML_SLOTS}) == len(ML_SLOTS)


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
    assert len({_window(c(PROBE)) for c in CTX}) == len(CTX)


def test_each_value_embedded_once_unmodified_in_its_assigned_context(h5, raw):
    assert len(h5) == len(raw)
    seen = defaultdict(int)
    covered = defaultdict(set)
    for s, r in zip(h5, raw, strict=True):
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
    assert sum(s.category == "credential-slot" for s in h5) == 250


def test_credential_slot_values_only_in_slot_contexts_and_negatives_in_none(h5):
    affixes = [tuple(c(PROBE).split(PROBE)) for c in CTX]

    def contexts_of(text):
        return {i for i, (pre, suf) in enumerate(affixes)
                if text.startswith(pre) and text.endswith(suf) and len(text) >= len(pre) + len(suf)}

    slot_samples = [s for s in h5 if s.category == "credential-slot"]
    assert slot_samples
    for s in slot_samples:
        assert len(contexts_of(s.text)) == 1 and contexts_of(s.text) <= SLOTS
    assert {i for s in slot_samples for i in contexts_of(s.text)} & ML_SLOTS == ML_SLOTS
    negs = [s for s in h5 if s.expected == "negative"]
    assert negs
    for s in negs:
        assert s.secret is None and not contexts_of(s.text)


APPROVED_LICENCES = {"MIT", "BSD-2-Clause", "BSD-3-Clause", "Apache-2.0", "PSF-2.0", "MIT OR Apache-2.0"}
EARLIER_NEG_DIRS = ("negatives", "negatives_external", "negatives_holdout2", "negatives_holdout3",
                    "negatives_holdout4")


@pytest.fixture(scope="module")
def neg():
    return json.loads((NEG_DIR / "MANIFEST.json").read_text())


def test_negatives_match_manifest(neg):
    on_disk = {p.relative_to(NEG_DIR).as_posix() for p in NEG_DIR.rglob("*")
               if p.is_file() and p.name != "MANIFEST.json" and not p.relative_to(NEG_DIR).as_posix().startswith("LICENSES/")}
    paths = {f["path"] for f in neg["files"]}
    assert on_disk == paths and len(paths) == len(neg["files"]) == 150
    assert len({f["sha256"] for f in neg["files"]}) == len(neg["files"])
    for f in neg["files"]:
        data = (NEG_DIR / f["path"]).read_bytes()
        assert data and len(data) == f["bytes"] <= 16384 and hashlib.sha256(data).hexdigest() == f["sha256"], f
    c = neg["composition"]
    assert c["total"] == len(neg["files"]) == c["stdlib"] + c["third_party"] and c["stdlib"] == 0
    assert [{"path": f["path"], "sha256": f["sha256"]} for f in neg["files"]] == MANIFEST["negatives"]["files"]
    assert MANIFEST["negatives"]["count"] == len(neg["files"])
    assert neg["prescan"]["excluded_as_real_credentials"] == len(neg["real_credential_exclusions"])
    assert neg["prescan"]["candidates_scanned"] >= neg["selection"]["pool"] >= len(neg["files"])


def test_negatives_disjoint_from_every_earlier_set_and_the_h4_first_draw(neg):
    paths = {f["path"] for f in neg["files"]}
    originals = {f["original"].removeprefix("site-packages/") for f in neg["files"]}
    hashes = {f["sha256"] for f in neg["files"]}
    for sub in EARLIER_NEG_DIRS:
        earlier = json.loads((CORPUS_DIR / sub / "MANIFEST.json").read_text())["files"]
        assert not paths & {f["path"] for f in earlier}, sub
        assert not originals & {f["path"].split("/", 1)[-1] for f in earlier}, sub
        assert not hashes & {f["sha256"] for f in earlier}, sub
    h4 = json.loads((CORPUS_DIR / "HOLDOUT4_MANIFEST.json").read_text())
    first = json.loads((CORPUS_DIR / "HOLDOUT4_FIRST_DRAW_NEGATIVES.json").read_text())
    withdrawn = h4["negatives"]["withdrawn_first_draw"]["files"]
    assert [f["path"] for f in withdrawn] == first["paths"]
    assert not paths & set(first["paths"]) and not originals & set(first["paths"])
    assert not hashes & {f["sha256"] for f in withdrawn}


def test_every_negative_has_an_approved_licence_and_licence_file_and_no_package_dominates(neg):
    lic = NEG_DIR / "LICENSES"
    pkgs = neg["packages"]
    for f in neg["files"]:
        dist, version = f["origin"].split(" ")
        p = pkgs[dist]
        assert p["version"] == version and f["licence"] == p["licence"] in APPROVED_LICENCES, f["path"]
        assert f["path"].startswith(f"{dist}-{version}/"), f["path"]
        low = f["path"].lower()
        assert not any(x in low for x in ("test", "vendor", "third_party", "externals")), f["path"]
    for dist, p in pkgs.items():
        assert p["licence_files"] and all((NEG_DIR / n).is_file() and n.startswith(f"LICENSES/{dist}-{p['version']}-")
                                          for n in p["licence_files"]), dist
        assert p["count"] == sum(f["origin"] == f"{dist} {p['version']}" for f in neg["files"]) >= 1, dist
        assert p["count"] <= 0.15 * len(neg["files"]), dist
    assert sum(p["count"] for p in pkgs.values()) == neg["composition"]["third_party"]
    listed = {n for p in pkgs.values() for n in p["licence_files"]}
    assert {p.relative_to(NEG_DIR).as_posix() for p in lic.iterdir()} == listed


def test_manifest_hashes(h5):
    sha = MANIFEST["sha256"]
    assert MANIFEST["seed"] == holdout_5.HOLDOUT5_SEED
    assert digest(h5) == sha["corpus_digest"]
    assert hashlib.sha256((CORPUS_DIR / "holdout_5.py").read_bytes()).hexdigest() == sha["holdout_5_py_sha256"]
    assert hashlib.sha256((NEG_DIR / "MANIFEST.json").read_bytes()).hexdigest() == sha["negatives_manifest_sha256"]
    parts = {k: sha[k] for k in ("corpus_digest", "holdout_5_py_sha256", "negatives_manifest_sha256")}
    assert hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest() == sha["total"]
