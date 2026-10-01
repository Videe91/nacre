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
    # History: R3 measured 32/200 under R1 (the trusted expected failures). Under R4 all 50 expected failures flag,
    # trusted or not, because none of v1's predictions names a failing check (vague by construction).
    assert (measured.episodes, measured.flagged) == (200, 50)
    eps = {e["id"]: e for e in _episodes()}
    assert all(not eps[i]["outcome"]["success"] for i in measured.flagged_ids)


def test_v1_is_historical_its_unnamed_predictions_are_vague_under_r4(measured):
    # R3 measured 32/200 under R1. Under R4 the same 32 still flag: v1 predictions name no failing check, so they are
    # "vague" by construction. Item 7 is therefore confirmed on v2 (D-0019 R4), not v1.
    assert not item7_passes(True, measured)


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


V2 = ROOT / "tests" / "regression" / "routine" / "routine_episodes_v2.json"
V2_SHA = "b1900a51f54f9797f94bb1d61f9c62190d7ae9196da68d23694d44d2a1361aad"


def test_v2_is_the_frozen_blind_set_and_regenerates():
    assert hashlib.sha256(V2.read_bytes()).hexdigest() == V2_SHA
    spec = importlib.util.spec_from_file_location("gen2", ROOT / "scripts" / "make_routine_episodes_v2.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    assert json.dumps(gen.build(), indent=1) + "\n" == V2.read_text()


def _recall_complete(session, streams, provider):
    """A-0028 recomputed under the current gate: every lesson-bearing episode of 003-016 is flagged."""
    from nacre.eval.load_mnexa_family import load_mnexa_family
    from nacre.gate.flag_events import flag_events
    manifest = json.loads((ROOT / "tests/regression/mnexa/MANIFEST.json").read_text())
    run = {e["experiment"]: d for d, e in manifest["runs"].items() if e.get("experiment")}
    for n in range(3, 17):
        fams = json.loads((ROOT / f"tests/regression/mnexa/tasks/tasks_{n:03d}.json").read_text())["families"]
        live = {}
        if n in (3, 4):
            rows = json.loads((ROOT / f"tests/regression/mnexa/results/{run[f'seed-growth-{n:03d}']}.result.json").read_text())["families"]
            live = {r.get("family_id") or r.get("id"): r["experience"]["decision"] for r in rows}
        stream = streams["b"]
        with session(uuid.uuid4(), read=[stream], write=[stream]) as s:
            loaded = [load_mnexa_family(s, provider, stream, f, n, decision_text=live.get(f["id"])) for f in fams]
            flagged = {f.target_event_id for f in flag_events(s, provider, stream)}
            already = {r[0] for r in s.conn.execute(
                "SELECT caused_by FROM ledger.events WHERE stream_id = %s AND event_type = 'memory_event'", (stream,))}
        if not all(lf.outcome_id in flagged | already for lf in loaded if not lf.success):
            return False
    return True


def test_item7_is_confirmed_on_v2_under_r4(session, streams, provider):
    from nacre.eval.measure_gate_selectivity import item7_v2_passes, measure_v2
    eps = json.loads(V2.read_text())["episodes"]
    with session(uuid.uuid4(), read=[streams["a"]], write=[streams["a"]]) as s:
        r = measure_v2(s, provider, streams["a"], eps)
    assert (r.routine, len(r.routine_flagged), r.adversarial, r.adversarial_missed) == (200, 0, 75, ())   # recorded result
    assert item7_v2_passes(_recall_complete(session, streams, provider), r)


def test_canary_v2_a_flag_everything_gate_fails_item7(session, streams, provider, monkeypatch):
    from nacre.core.event import Mode
    from nacre.eval.measure_gate_selectivity import item7_v2_passes, measure_v2
    from nacre.gate import flag_events as fe
    monkeypatch.setitem(fe.THRESHOLDS, Mode.NORMAL, 0)
    with session(uuid.uuid4(), read=[streams["a"]], write=[streams["a"]]) as s:
        r = measure_v2(s, provider, streams["a"], json.loads(V2.read_text())["episodes"])
    assert len(r.routine_flagged) == 200 and not item7_v2_passes(True, r)
