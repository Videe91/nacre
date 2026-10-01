"""Tests for gate/flag_events.py: thresholds by mode, idempotency, concurrency safety."""
import threading

from gate_kit import REVIEW, TOOL, Mode, episode
from nacre.core.event import EventType
from nacre.gate.flag_events import THRESHOLD_VERSION, flag_events
from nacre.ledger.read_stream import read_stream


def _flags(s, provider, stream):
    return [e for e in read_stream(s, provider, stream) if e.envelope.event_type == EventType.MEMORY_EVENT
            and e.body["content"].get("op") == "flag"]


def test_flags_go_only_to_events_at_or_above_threshold(rw, provider, streams):
    with rw() as s:
        hit = episode(s, provider, streams["a"], success=False, sections=[("correction", "do X")], who=REVIEW, expected_success=False)
        miss = episode(s, provider, streams["a"], success=True, sections=[("status", "ok")], who=TOOL)
        created = flag_events(s, provider, streams["a"])
        (f,) = _flags(s, provider, streams["a"])
    assert [x.target_event_id for x in created] == [hit.event_id] and miss
    c = f.body["content"]
    assert f.envelope.caused_by == hit.event_id and c["threshold_version"] == THRESHOLD_VERSION
    assert (c["score"], c["surprise"], c["threshold"], c["mode"]) == (1000, 1000, 500, "normal")


def test_incident_mode_flags_everything_scorable(rw, provider, streams):
    with rw() as s:
        episode(s, provider, streams["a"], success=True, sections=[("status", "ok")], who=TOOL, mode=Mode.INCIDENT)
        assert len(flag_events(s, provider, streams["a"])) == 1


def test_flagging_twice_is_idempotent_and_from_seq_limits_the_scan(rw, provider, streams):
    with rw() as s:
        episode(s, provider, streams["a"], success=False, sections=[("status", "x")], who=TOOL)
        assert len(flag_events(s, provider, streams["a"])) == 1
        assert flag_events(s, provider, streams["a"]) == []
        later = episode(s, provider, streams["a"], success=False, sections=[("status", "y")], who=TOOL)
        assert flag_events(s, provider, streams["a"], from_seq=later.commit_seq + 1) == []
        assert [f.target_event_id for f in flag_events(s, provider, streams["a"])] == [later.event_id]


def test_concurrent_flaggers_do_not_double_flag(rw, provider, streams):
    with rw() as s:
        episode(s, provider, streams["a"], success=False, sections=[("status", "x")], who=TOOL)
    errors = []

    def run():
        try:
            with rw() as s:
                flag_events(s, provider, streams["a"])
        except Exception as exc:              # surfaced below
            errors.append(exc)
    threads = [threading.Thread(target=run) for _ in range(4)]
    [t.start() for t in threads]
    [t.join(30) for t in threads]
    with rw() as s:
        assert errors == [] and len(_flags(s, provider, streams["a"])) == 1
