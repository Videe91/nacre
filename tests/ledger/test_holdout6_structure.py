"""Structure-only checks of sealed holdout H6 (pre-registered revised gate item 12). Never calls a detector or a
measurement.

Checks: context count and slot labelling; every slot context classified common or long-tail (and no non-slot),
>= 9 of each, every owner-listed common format covered by a common slot; >= 3 command-line, >= 3 attribute-pair and
>= 3 heredoc/multi-line slot shapes; >= 2 non-slot contexts with benign high-entropy values beside version/commit/
checksum/id words; disjointness from every earlier context (working set, H1-H5); each value embedded exactly once
and unmodified in the context the build assigns; credential-slot values only in slot contexts; synthetic negatives
raw; the 150 real-code negatives matching their manifest, disjoint by path, original path and content sha256 from
negatives/, negatives_external/, H2-H5 and the H4 withdrawn first draw, every file under an approved licence with its
licence file and no package over 15% of the set; and the HOLDOUT6_MANIFEST.json hashes."""
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

import pytest

from secret_corpus import holdout_2, holdout_3, holdout_4, holdout_5, holdout_6
from secret_corpus.corpus import CONTEXTS, GENERATORS, HOLDOUT_CONTEXTS, build, digest

pytestmark = pytest.mark.holdout

CORPUS_DIR = Path(__file__).resolve().parent / "secret_corpus"
MANIFEST = json.loads((CORPUS_DIR / "HOLDOUT6_MANIFEST.json").read_text())
NEG_DIR = CORPUS_DIR / "negatives_holdout6"
PROBE = "PROBE-VALUE-123"
CTX = holdout_6.HOLDOUT6_CONTEXTS
SLOTS = holdout_6.HOLDOUT6_CREDENTIAL_SLOTS
CLASS = holdout_6.HOLDOUT6_CONTEXT_CLASS
CL = holdout_6.HOLDOUT6_COMMAND_LINE_SLOTS
AP = holdout_6.HOLDOUT6_ATTRIBUTE_PAIR_SLOTS
HD = holdout_6.HOLDOUT6_HEREDOC_SLOTS
BENIGN = holdout_6.HOLDOUT6_BENIGN_ENTROPY_NON_SLOTS
OWNER_COMMON = {".env", "YAML", "JSON", "TOML", ".properties", "Dockerfile", "Kubernetes manifest", "CLI flag",
                "connection string"}
EARLIER = (CONTEXTS + HOLDOUT_CONTEXTS + holdout_2.HOLDOUT2_CONTEXTS + holdout_3.HOLDOUT3_CONTEXTS
           + holdout_4.HOLDOUT4_CONTEXTS + holdout_5.HOLDOUT5_CONTEXTS)


@pytest.fixture(scope="module")
def h6():
    return holdout_6.build_holdout6()


@pytest.fixture(scope="module")
def raw():
    """The same build with identity contexts: contexts draw no randomness, so this yields the bare values
    (or documents) in the same order. Used to prove each H6 text is exactly context(value)."""
    return build(seed=holdout_6.HOLDOUT6_SEED, per_generator=50, contexts=[lambda v: v] * len(CTX),
                 embed_documents=True, cover_all_contexts=True, credential_slots=SLOTS, embed_negatives=False)


def _line(i):
    (line,) = [ln for ln in CTX[i](PROBE).splitlines() if PROBE in ln]
    return line


def test_context_count_and_slot_labelling():
    assert len(CTX) == 30
    assert SLOTS == frozenset(range(22))
    assert len(SLOTS) >= 18 and 7 <= len(CTX) - len(SLOTS) <= 9
    c = MANIFEST["contexts"]
    assert c["count"] == len(CTX) and c["credential_slots"] == sorted(SLOTS)
    assert c["non_slot"] == sorted(set(range(len(CTX))) - SLOTS)
    assert c["command_line_slots"] == sorted(CL) and c["attribute_pair_slots"] == sorted(AP)
    assert c["heredoc_slots"] == sorted(HD) and c["benign_entropy_non_slots"] == sorted(BENIGN)
    descriptions = [ln for ln in holdout_6.__doc__.splitlines() if re.match(r"\s*\d+\. \[", ln)]
    assert len(descriptions) == len(CTX)


def test_every_slot_classified_before_build_and_counts():
    assert set(CLASS) == set(SLOTS) and set(CLASS.values()) == {"common", "long-tail"}
    common = {i for i in CLASS if CLASS[i] == "common"}
    long_tail = set(CLASS) - common
    assert len(common) >= 9 and len(long_tail) >= 9
    assert MANIFEST["contexts"]["class"] == {str(i): CLASS[i] for i in sorted(CLASS)}
    assert MANIFEST["contexts"]["common"] == sorted(common)
    assert MANIFEST["contexts"]["long_tail"] == sorted(long_tail)
    # every owner-listed format is covered by a common slot, and owner-format contexts are all common
    fmt = holdout_6.HOLDOUT6_OWNER_FORMAT
    assert set(fmt.values()) == OWNER_COMMON and set(fmt) <= common
    # the docstring states the rule and its prevalence sources
    doc = holdout_6.__doc__
    assert "survey.stackoverflow.co/2025" in doc and "Octoverse" in doc and "sealed 2026-10-02" in doc


def test_slot_shapes():
    assert len(CL) >= 3 and len(AP) >= 3 and len(HD) >= 3
    assert CL <= SLOTS and AP <= SLOTS and HD <= SLOTS
    assert not (CL & AP) and not (CL & HD) and not (AP & HD)
    for i in HD:                                               # value on its own line between opener and closer
        lines = CTX[i](PROBE).splitlines()
        (j,) = [n for n, ln in enumerate(lines) if PROBE in ln]
        assert lines[j].strip() == PROBE, i
        assert 0 < j < len(lines) - 1, i
        assert re.search(r"<<|'''|\"\"\"", lines[j - 1]), i    # the opener is on the line above
    assert len({CTX[i](PROBE).splitlines()[0] for i in HD}) == len(HD)   # distinct languages/formats
    for i in AP:                                   # name in one field, the value in a sibling field (same entry)
        line = _line(i)
        entry = "\n".join(_window(CTX[i](PROBE))[:2])
        assert re.search(r"\b(key|name)\b", entry) and re.search(r"\bvalue\b", line), i
        assert line.strip() != PROBE
    for i in CL:                                               # a command or protocol line holding the value
        line = _line(i).strip()
        assert line != PROBE and " " in line.split(PROBE)[0], i
    for i in SLOTS - HD:                                       # every other slot shares its line with the name
        assert _line(i).strip() != PROBE, i


def test_benign_entropy_non_slots():
    assert len(BENIGN) >= 2 and not BENIGN & SLOTS
    hexrun = re.compile(r"\b[0-9a-f]{40,64}\b")
    for i in BENIGN:
        text = CTX[i](PROBE)
        assert hexrun.search(text), i
        assert re.search(r"\b(version|commit|checksum|id)\b", text), i


def _window(text):
    """The probe's line with its neighbours: the local context a detector sees around the value."""
    lines = text.splitlines()
    (j,) = [n for n, ln in enumerate(lines) if PROBE in ln]
    return tuple(lines[max(j - 1, 0):j + 2])


def test_contexts_disjoint_from_every_earlier_holdout():
    earlier_full = {c(PROBE) for c in EARLIER}
    earlier_windows = {_window(c(PROBE)) for c in EARLIER}
    earlier_lines = {ln for c in EARLIER for ln in c(PROBE).splitlines() if PROBE in ln}
    for i, c in enumerate(CTX):
        text = c(PROBE)
        assert text.count(PROBE) == 1, i
        assert text not in earlier_full, i
        assert _window(text) not in earlier_windows, i
        assert _line(i) not in earlier_lines or _line(i).strip() == PROBE, i
    assert len({c(PROBE) for c in CTX}) == len(CTX)
    assert len({_window(c(PROBE)) for c in CTX}) == len(CTX)


def test_each_value_embedded_once_unmodified_in_its_assigned_context(h6, raw):
    assert len(h6) == len(raw)
    seen = defaultdict(int)
    covered = defaultdict(set)
    for s, r in zip(h6, raw, strict=True):
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
    assert sum(s.category == "credential-slot" for s in h6) == 250


def test_credential_slot_values_only_in_slot_contexts_and_negatives_in_none(h6):
    affixes = [tuple(c(PROBE).split(PROBE)) for c in CTX]

    def contexts_of(text):
        return {i for i, (pre, suf) in enumerate(affixes)
                if text.startswith(pre) and text.endswith(suf) and len(text) >= len(pre) + len(suf)}

    slot_samples = [s for s in h6 if s.category == "credential-slot"]
    assert slot_samples
    for s in slot_samples:
        assert len(contexts_of(s.text)) == 1 and contexts_of(s.text) <= SLOTS
    by_class = defaultdict(int)
    for s in slot_samples:
        (i,) = contexts_of(s.text)
        by_class[CLASS[i]] += 1
    assert by_class["common"] > 0 and by_class["long-tail"] > 0
    assert {i for s in slot_samples for i in contexts_of(s.text)} == set(SLOTS)
    negs = [s for s in h6 if s.expected == "negative"]
    assert negs
    for s in negs:
        assert s.secret is None and not contexts_of(s.text)


APPROVED_LICENCES = {"MIT", "BSD-2-Clause", "BSD-3-Clause", "Apache-2.0", "PSF-2.0", "MIT OR Apache-2.0"}
EARLIER_NEG_DIRS = ("negatives", "negatives_external", "negatives_holdout2", "negatives_holdout3",
                    "negatives_holdout4", "negatives_holdout5")


@pytest.fixture(scope="module")
def neg():
    return json.loads((NEG_DIR / "MANIFEST.json").read_text())


def test_negatives_match_manifest(neg):
    on_disk = {p.relative_to(NEG_DIR).as_posix() for p in NEG_DIR.rglob("*")
               if p.is_file() and p.name != "MANIFEST.json"
               and not p.relative_to(NEG_DIR).as_posix().startswith("LICENSES/")}
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
        earlier_originals = ({f["path"].split("/", 1)[-1] for f in earlier}
                             | {f["original"].removeprefix("site-packages/") for f in earlier if f.get("original")})
        assert not originals & earlier_originals, sub
        assert not hashes & {f["sha256"] for f in earlier}, sub
    h4 = json.loads((CORPUS_DIR / "HOLDOUT4_MANIFEST.json").read_text())
    first = json.loads((CORPUS_DIR / "HOLDOUT4_FIRST_DRAW_NEGATIVES.json").read_text())
    withdrawn = h4["negatives"]["withdrawn_first_draw"]["files"]
    assert [f["path"] for f in withdrawn] == first["paths"]
    assert not paths & set(first["paths"]) and not originals & set(first["paths"])
    assert not originals & {p.split("/", 1)[-1] for p in first["paths"]}
    assert not hashes & {f["sha256"] for f in withdrawn}


def test_every_negative_has_an_approved_licence_and_licence_file_and_no_package_dominates(neg):
    lic = NEG_DIR / "LICENSES"
    pkgs = neg["packages"]
    for f in neg["files"]:
        p = pkgs[f["dist"]]
        assert p["version"] == f["version"] and f["licence"] == p["licence"] in APPROVED_LICENCES, f["path"]
        assert f["path"] == f"{p['dir']}/{f['original'].removeprefix('site-packages/')}", f["path"]
        low = f["path"].lower()
        assert not any(x in low for x in ("test", "vendor", "third_party", "externals")), f["path"]
    for dist, p in pkgs.items():
        assert p["licence_files"] and all((NEG_DIR / n).is_file() and n.startswith(f"LICENSES/{p['dir']}-")
                                          for n in p["licence_files"]), dist
        assert p["count"] == sum(f["dist"] == dist for f in neg["files"]) >= 1, dist
        assert p["count"] <= 0.15 * len(neg["files"]), dist
    assert sum(p["count"] for p in pkgs.values()) == neg["composition"]["third_party"]
    listed = {n for p in pkgs.values() for n in p["licence_files"]}
    assert {p.relative_to(NEG_DIR).as_posix() for p in lic.iterdir()} == listed


def test_manifest_hashes(h6):
    sha = MANIFEST["sha256"]
    assert MANIFEST["seed"] == holdout_6.HOLDOUT6_SEED
    assert digest(h6) == sha["corpus_digest"]
    assert hashlib.sha256((CORPUS_DIR / "holdout_6.py").read_bytes()).hexdigest() == sha["holdout_6_py_sha256"]
    assert hashlib.sha256((NEG_DIR / "MANIFEST.json").read_bytes()).hexdigest() == sha["negatives_manifest_sha256"]
    parts = {k: sha[k] for k in ("corpus_digest", "holdout_6_py_sha256", "negatives_manifest_sha256")}
    assert hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest() == sha["total"]
