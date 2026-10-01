"""Tests for eval/load_mnexa_family.py, plus the A-0028 check (Phase 2 gate item 7): through this real capture mapping,
the write gate flags 100% of the lesson-bearing episodes of every frozen family 003-016."""
import json
import uuid
from pathlib import Path

import pytest

from nacre.capture.section_authority import section_authority
from nacre.eval.load_mnexa_family import MappingError, load_mnexa_family, outcome_sections
from nacre.gate.flag_events import flag_events
from nacre.ledger.read_stream import read_stream

R = Path(__file__).resolve().parents[2] / "tests" / "regression" / "mnexa"
MANIFEST = json.loads((R / "MANIFEST.json").read_text())
RUN = {e["experiment"]: d for d, e in MANIFEST["runs"].items() if e.get("experiment")}


def _families(n):
    return json.loads((R / "tasks" / f"tasks_{n:03d}.json").read_text())["families"]


def _live_first_decisions(n):
    rows = json.loads((R / "results" / f"{RUN[f'seed-growth-{n:03d}']}.result.json").read_text())["families"]
    return {r.get("family_id") or r.get("id"): r["experience"]["decision"] for r in rows}


def test_role_regions_map_to_sections_without_markers():
    fam = _families(14)[0]
    sections, success = outcome_sections(fam, 14)
    assert success is False and [s.role for s in sections] == ["status", "diagnostic", "correction", "operator_note", "diagnostic"]
    assert all("<<<" not in s.text and ">>>" not in s.text for s in sections)
    assert any(fam["semantic_clause"].split()[0] in s.text for s in sections if s.role == "correction")


def test_text_outside_regions_or_unknown_roles_are_refused():
    fam = dict(_families(14)[0])
    with pytest.raises(MappingError, match="outside"):
        outcome_sections(dict(fam, raw_source=fam["raw_source"] + "\nloose text"), 14)
    with pytest.raises(MappingError, match="unmapped"):
        outcome_sections(dict(fam, raw_source="<<<ROLE:oracle>>>x<<<END_ROLE:oracle>>>"), 14)
    with pytest.raises(MappingError, match="live first decision"):
        outcome_sections(_families(3)[0], 3)
    with pytest.raises(MappingError, match="no pre-registered"):
        outcome_sections(fam, 17)


def test_the_loaded_outcome_is_trusted_and_only_its_correction_is_authoritative(session, streams, provider):
    with session(uuid.uuid4(), read=[streams["a"]], write=[streams["a"]]) as s:
        lf = load_mnexa_family(s, provider, streams["a"], _families(15)[0], 15)
        (o,) = [e for e in read_stream(s, provider, streams["a"]) if e.envelope.event_id == lf.outcome_id]
    auth = [section_authority(o.envelope, x["role"], False).authoritative for x in o.body["content"]["sections"]]
    assert auth == [False, False, True, False, False]


@pytest.mark.parametrize("n", range(3, 17))
def test_a0028_the_gate_flags_every_lesson_bearing_episode(session, streams, provider, n):
    live = _live_first_decisions(n) if n in (3, 4) else {}
    with session(uuid.uuid4(), read=[streams["a"]], write=[streams["a"]]) as s:
        loaded = [load_mnexa_family(s, provider, streams["a"], f, n, decision_text=live.get(f["id"])) for f in _families(n)]
        flagged = {f.target_event_id for f in flag_events(s, provider, streams["a"])}
    bearing = [lf for lf in loaded if not lf.success]
    assert bearing and all(lf.outcome_id in flagged for lf in bearing), (n, len(bearing), len(flagged))
