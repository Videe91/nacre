"""Tests for gate/score_event.py: every D-0019 rule, including R1."""
import pytest

from gate_kit import REVIEW, TOOL, episode, person_correction
from nacre.gate.score_event import score_event
from nacre.ledger.read_stream import read_stream


def _score(s, provider, stream, env):
    (e,) = [x for x in read_stream(s, provider, stream) if x.envelope.event_id == env.event_id]
    return score_event(s, provider, e)


@pytest.mark.parametrize("success,expected,who,sections,surprise", [
    (False, "none", TOOL, [("status", "FAIL")], 1000),               # unpredicted failure
    (False, True, TOOL, [("status", "FAIL")], 1000),                 # predicted success, failed
    (False, False, TOOL, [("status", "FAIL")], 0),                   # predicted failure, failed, no authority
    (True, False, TOOL, [("status", "OK")], 1000),                   # predicted failure, succeeded
    (True, True, TOOL, [("status", "OK")], 0),
    (None, "none", TOOL, [("status", "?")], 0),
    (False, False, REVIEW, [("correction", "do X")], 1000),          # R1: authoritative correction beats the prediction
    (None, "none", REVIEW, [("correction", "do X")], 1000),          # R1 with unknown success
    (True, True, REVIEW, [("evaluation", "fine")], 0),               # a passing evaluation is not authority
    (False, False, TOOL, [("correction", "do X")], 0),               # untrusted correction is not authority
])
def test_surprise_rules(rw, provider, streams, success, expected, who, sections, surprise):
    with rw() as s:
        o = episode(s, provider, streams["a"], success=success, sections=sections, who=who, expected_success=expected)
        sc = _score(s, provider, streams["a"], o)
    assert sc.surprise == surprise and sc.score == max(sc.surprise, sc.stakes, sc.statement)


def test_stakes_on_the_decision_or_the_outcome(rw, provider, streams):
    with rw() as s:
        a = episode(s, provider, streams["a"], success=True, sections=[("status", "ok")], who=TOOL, decision_stakes=("money",))
        b = episode(s, provider, streams["a"], success=True, sections=[("status", "ok")], who=TOOL, outcome_stakes=("client",))
        c = episode(s, provider, streams["a"], success=True, sections=[("status", "ok")], who=TOOL)
        assert [_score(s, provider, streams["a"], x).stakes for x in (a, b, c)] == [1000, 1000, 0]


def test_a_trusted_person_correction_is_a_statement_signal(rw, provider, streams):
    with rw() as s:
        o = episode(s, provider, streams["a"], success=True, sections=[("status", "ok")], who=TOOL)
        c = person_correction(s, provider, streams["a"], o.event_id)
        sc = _score(s, provider, streams["a"], c)
    assert sc.statement == 1000 and sc.score == 1000


def test_unscorable_events_score_zero(rw, provider, streams):
    with rw() as s:
        o = episode(s, provider, streams["a"], success=False, sections=[("status", "x")], who=TOOL)
        (d,) = [x for x in read_stream(s, provider, streams["a"]) if x.envelope.event_type.value == "decision"]
        assert score_event(s, provider, d).score == 0 and o


def test_statements_need_a_trusted_event_and_a_person(rw, provider, streams):
    import uuid
    from nacre.capture.record_correction import record_correction
    from nacre.core.event import ActorKind, Source
    from nacre.ledger.append_event import Authorship
    with rw() as s:
        o = episode(s, provider, streams["a"], success=True, sections=[("status", "ok")], who=TOOL)
        untrusted = record_correction(s, provider, stream_id=streams["a"], idempotency_key=str(uuid.uuid4()),
                                      correction_of=o.event_id, text="no", actor_kind=ActorKind.PERSON,
                                      actor_id=uuid.uuid4(), source=Source.WEB, authorship=Authorship.EXTERNAL).envelope
        agent = record_correction(s, provider, stream_id=streams["a"], idempotency_key=str(uuid.uuid4()),
                                  correction_of=o.event_id, text="no", actor_kind=ActorKind.AGENT,
                                  actor_id=uuid.uuid4(), source=Source.CHAT, authorship=Authorship.SCOPE_PRINCIPAL).envelope
        assert [_score(s, provider, streams["a"], x).statement for x in (untrusted, agent)] == [0, 0]
