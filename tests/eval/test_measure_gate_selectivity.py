"""Gate item 7 selectivity (D-0019 R3, pre-registered in 9b85e90 before this measurement)."""
import hashlib
import importlib.util
import json
import uuid
from pathlib import Path

import pytest

from nacre.eval.measure_gate_selectivity import CEILING_PERMILLE, item7_passes, measure_selectivity

ROOT = Path(__file__).resolve().parents[2]
SET = ROOT / "tests" / "regression" / "routine" / "routine_episodes_v1.json"
FROZEN_SHA = "cad974a7dc541cb496f33918520835010d7b349b71562f5e54b36679c751e8f6"


def _episodes():
    return json.loads(SET.read_text())["episodes"]


def test_the_routine_set_is_frozen_and_regenerates_byte_for_byte():
    assert hashlib.sha256(SET.read_bytes()).hexdigest() == FROZEN_SHA
    spec = importlib.util.spec_from_file_location("gen", ROOT / "scripts" / "make_routine_episodes.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    assert json.dumps(gen.build(), indent=1) + "\n" == SET.read_text()


def test_the_set_matches_its_definition():
    for ep in _episodes():
        o, p = ep["outcome"], ep["prediction"]
        assert p["expected_success"] == o["success"]                              # prediction correct
        assert all(s["role"] != "correction" for s in o["sections"]) and "stakes" not in o


@pytest.fixture
def measured(session, streams, provider):
    with session(uuid.uuid4(), read=[streams["a"]], write=[streams["a"]]) as s:
        return measure_selectivity(s, provider, streams["a"], _episodes())


def test_measured_selectivity_is_recorded(measured):
    # Recorded result of the registered measurement (see D-0019 R3 "Measured"). Changes here need a new registration.
    assert (measured.episodes, measured.flagged) == (200, 32)
    eps = {e["id"]: e for e in _episodes()}
    assert all(not eps[i]["outcome"]["success"] and eps[i]["outcome"]["source"] in ("ci", "review") for i in measured.flagged_ids)


@pytest.mark.xfail(strict=True, reason="OPEN: R1's failing-evaluation clause flags 32/200 (16%) routine expected "
                   "failures, over the 5% ceiling. Owner decision pending (D-0019 R3).")
def test_item7_passes_with_full_recall(measured):
    assert item7_passes(True, measured)


def test_canary_a_flag_everything_gate_fails_item7(session, streams, provider, monkeypatch):
    from nacre.core.event import Mode
    from nacre.gate import flag_events as fe
    monkeypatch.setitem(fe.THRESHOLDS, Mode.NORMAL, 0)
    with session(uuid.uuid4(), read=[streams["a"]], write=[streams["a"]]) as s:
        report = measure_selectivity(s, provider, streams["a"], _episodes())
    assert report.flagged == 200 and not item7_passes(True, report)


def test_item7_needs_recall_and_respects_the_ceiling():
    from nacre.eval.measure_gate_selectivity import SelectivityReport
    assert item7_passes(True, SelectivityReport(200, 10, ())) and not item7_passes(True, SelectivityReport(200, 11, ()))
    assert not item7_passes(False, SelectivityReport(200, 0, ())) and CEILING_PERMILLE == 50
