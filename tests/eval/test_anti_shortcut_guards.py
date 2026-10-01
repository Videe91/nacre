"""Phase 2 gate item 11, anti-shortcut guard (i): product code (src/nacre outside eval/) never sees task data and
never parses role markers. Guards (ii) and (iii) need the sleep pass and are added with it (tests/sleep/)."""
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "nacre"
FORBIDDEN = re.compile(r"raw_source|semantic_clause|fallback_challenges|<<<ROLE|END_ROLE|candidate_decision|"
                       r"tasks_0\d\d|_grader\b|semantic_grade|grade_text|authoritative_correction|regression/mnexa|"
                       r"exp0001|knowledge_grader|nacre\.eval")


def _product_files():
    return [p for p in SRC.rglob("*.py") if "eval" not in p.relative_to(SRC).parts]


def test_no_product_module_references_task_data_markers_or_the_harness():
    offenders = {str(p.relative_to(SRC)): sorted(set(FORBIDDEN.findall(p.read_text()))) for p in _product_files()
                 if FORBIDDEN.search(p.read_text())}
    assert offenders == {}


def test_the_guard_would_catch_a_violation(tmp_path):
    assert FORBIDDEN.search('x = family["raw_source"]') and FORBIDDEN.search("from nacre.eval.grade_decision import x")
    assert FORBIDDEN.search("re.compile('<<<ROLE:(.*)')") and len(_product_files()) > 50
