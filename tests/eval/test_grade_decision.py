"""L1 instrument validation (D-0016 amendment 2): the ported grader reproduces every verdict MNEXA recorded on the
stored decisions of its frozen runs 003-016, exactly. Plus contract tests for each grader."""
import json
from pathlib import Path

import pytest

from nacre.eval.grade_decision import contains_any_regex, grade_text, semantic_grade

R = Path(__file__).resolve().parents[2] / "tests" / "regression" / "mnexa"
MANIFEST = json.loads((R / "MANIFEST.json").read_text())
RUN = {e["experiment"]: d for d, e in MANIFEST["runs"].items() if e.get("experiment")}


def _tasks(n):
    return {f["id"]: f for f in json.loads((R / "tasks" / f"tasks_{n:03d}.json").read_text())["families"]}


def _results(n):
    return json.loads((R / "results" / f"{RUN[f'seed-growth-{n:03d}']}.result.json").read_text())["families"]


def _fid(row):
    return row.get("family_id") or row.get("id")


def _v005(fam, text):            # 005/006: correct_grader + contamination
    c = grade_text(text, fam["correct_grader"]); x = contains_any_regex(text, fam["contamination_regex"])
    return {"correct": c, "contaminated": x, "success": c and not x}


def _v007(fam, text):
    c = grade_text(text, fam["correct_grader"]); t = grade_text(text, fam.get("transfer_grader", fam["correct_grader"]))
    x = contains_any_regex(text, fam["contamination_regex"])
    return {"complete": c, "transfer": t, "contaminated": x, "success": t and not x}


def _v008(fam, text):
    s = grade_text(text, fam["transfer_grader"]); leak = contains_any_regex(text, fam.get("nonknowledge_regex", []))
    return {"sufficient": s, "leak": leak, "success": s and not leak}


def _checks():
    """(set, family, label, recorded, recomputed) for every verdict MNEXA stored next to a decision text."""
    out = []
    add = lambda n, f, label, rec, got: out.append((n, f, label, rec, got))  # noqa: E731
    fams = _tasks(3)
    for row in _results(3):
        fam = fams[_fid(row)]
        add(3, fam["id"], "experience", row["experience"]["passed"], grade_text(row["experience"]["decision"], fam["experience"]["grader"]))
        for c in ("baseline", "mnexa"):
            add(3, fam["id"], c, row[c]["passed"], grade_text(row[c]["decision"], fam["transfer"]["grader"]))
    fams = _tasks(4)
    for row in _results(4):
        fam = fams[_fid(row)]
        add(4, fam["id"], "experience", row["experience"]["task_success"], grade_text(row["experience"]["decision"], fam["task_grader"]))
        for c in ("baseline", "mnexa_current", "mnexa_fidelity"):
            add(4, fam["id"], c + ".task", row[c]["task_success"], grade_text(row[c]["decision"], fam["task_grader"]))
            add(4, fam["id"], c + ".fidelity", row[c]["constraint_fidelity"], grade_text(row[c]["decision"], fam["fidelity_grader"]))
    for n, conds in ((5, ("mnexa_current_consolidation", "mnexa_disciplined_consolidation")),
                     (6, ("mnexa_evidence_disciplined", "mnexa_claim_ancestry"))):
        fams = _tasks(n)
        for row in _results(n):
            fam = fams[_fid(row)]
            b = _v005(fam, row["baseline"]["decision"])
            add(n, fam["id"], "baseline.correct", row["baseline"]["correct_knowledge"], b["correct"])
            add(n, fam["id"], "baseline.success", row["baseline"]["task_success"], b["success"])
            for c in conds:
                tr, le = _v005(fam, row[c]["transfer_decision"]), _v005(fam, row[c]["lesson"])
                add(n, fam["id"], c + ".transfer", row[c]["transfer_correct_knowledge"], tr["correct"])
                add(n, fam["id"], c + ".transfer_contaminated", row[c]["transfer_contaminated"], tr["contaminated"])
                add(n, fam["id"], c + ".success", row[c]["task_success"], tr["success"])
                add(n, fam["id"], c + ".lesson", row[c]["lesson_correct_knowledge"], le["correct"])
                add(n, fam["id"], c + ".lesson_contaminated", row[c]["lesson_contaminated"], le["contaminated"])
    fams = _tasks(7)
    for row in _results(7):
        fam = fams[_fid(row)]
        add(7, fam["id"], "baseline.success", row["baseline"]["task_success"], _v007(fam, row["baseline"]["decision"])["success"])
        for c in ("mnexa_oracle", "mnexa_raw_span"):
            tr, le = _v007(fam, row[c]["transfer_decision"]), _v007(fam, row[c]["lesson"])
            add(7, fam["id"], c + ".transfer", row[c]["transfer_correct_knowledge"], tr["transfer"])
            add(7, fam["id"], c + ".success", row[c]["task_success"], tr["success"])
            add(7, fam["id"], c + ".lesson", row[c]["lesson_complete_knowledge"], le["complete"])
            add(7, fam["id"], c + ".lesson_contaminated", row[c]["lesson_contaminated"], le["contaminated"])
    fams = _tasks(8)
    for row in _results(8):
        fam = fams[_fid(row)]
        b = _v008(fam, row["baseline"]["decision"])
        add(8, fam["id"], "baseline.sufficient", row["baseline"]["transfer_task_sufficient"], b["sufficient"])
        add(8, fam["id"], "baseline.success", row["baseline"]["task_success"], b["success"])
        for c in ("mnexa_span_only", "mnexa_role_gated"):
            t = _v008(fam, row[c]["transfer_decision"])
            add(8, fam["id"], c + ".sufficient", row[c]["transfer_task_sufficient"], t["sufficient"])
            add(8, fam["id"], c + ".leak", row[c]["transfer_nonknowledge_leak"], t["leak"])
            add(8, fam["id"], c + ".success", row[c]["task_success"], t["success"])
            add(8, fam["id"], c + ".lesson", row[c]["lesson_knowledge_complete"], grade_text(row[c]["lesson"], fam["knowledge_grader"]))
    for n, conds in ((9, ("mnexa_role_gated_current", "mnexa_atomicity_gated")), (10, ("oracle", "autonomous")),
                     (11, ("flat", "structured")), (12, ("initial", "repaired"))):
        fams = _tasks(n)
        for row in _results(n):
            fam = fams[_fid(row)]
            for c in conds:
                add(n, fam["id"], c + ".transfer", row[c]["transfer_task_sufficient"], grade_text(row[c]["transfer_decision"], fam["transfer_grader"]))
                add(n, fam["id"], c + ".lesson", row[c]["lesson_knowledge_complete"], grade_text(row[c]["lesson"], fam["knowledge_grader"]))
    for n, conds in ((13, ("current", "semantic_closed")), (14, ("current", "lossless")), (15, ("verbose", "compact")),
                     (16, ("verbose", "compact"))):
        fams = _tasks(n)
        for row in _results(n):
            fam = fams[_fid(row)]
            for c in conds:
                for i, rec in enumerate(row[c]["replicates"] if n == 16 else [row[c]]):
                    if "transfer_decision" not in rec:
                        continue
                    g = semantic_grade(rec["transfer_decision"], fam["semantic_grader"])
                    add(n, fam["id"], f"{c}[{i}].semantic", rec["semantic_task_pass"], g.passed)
                    add(n, fam["id"], f"{c}[{i}].required", rec["semantic_required_ok"], g.required_ok)
                    add(n, fam["id"], f"{c}[{i}].lesson", rec["lesson_knowledge_complete"], grade_text(rec["lesson"], fam["knowledge_grader"]))
    return out


CHECKS = _checks()


def test_every_recorded_mnexa_verdict_is_reproduced_exactly():
    wrong = [(n, f, label, rec, got) for n, f, label, rec, got in CHECKS if rec != got]
    assert not wrong, wrong[:10]


def test_the_validation_covers_every_set_and_is_not_trivial():
    sets = {n for n, *_ in CHECKS}
    assert sets == set(range(3, 17))
    assert len(CHECKS) > 1500
    recorded = [rec for *_, rec, _ in CHECKS]
    assert any(recorded) and not all(recorded)           # both verdicts occur, so agreement is informative


@pytest.mark.parametrize("text,grader,expected", [
    ("Use EXPONENTIAL backoff", {"all_of": [["exponential", "jitter"]]}, True),
    ("retry now", {"all_of": ["backoff"]}, False),
    ("backoff then retry", {"all_of": ["backoff"], "none_of": ["retry"]}, False),
    ("wait 137\nms", {"all_regex": [r"137\s*ms"]}, True),                       # DOTALL/whitespace
    ("MODE=ember-7", {"all_regex": [["mode=EMBER-7", "never"]]}, True),          # IGNORECASE, group alternatives
    ("drop it", {"none_regex": [r"drop\b"]}, False),
])
def test_grade_text_contract(text, grader, expected):
    assert grade_text(text, grader) is expected


def test_semantic_grade_contract():
    g = semantic_grade("wait 30s, then page oncall", {"all_regex": [["wait\\s+30"], ["page"]], "forbidden_regex": ["immediately"]})
    assert g.passed and g.required_ok and g.forbidden_hits == () and not g.violation
    g = semantic_grade("page immediately", {"all_regex": [["page"]], "forbidden_regex": ["immediately"]})
    assert not g.passed and g.required_ok and g.forbidden_hits == ("immediately",) and g.violation
