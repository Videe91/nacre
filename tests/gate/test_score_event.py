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
    (False, False, TOOL, [("status", "FAIL")], 1000),                # R4: predicted failure without a named check
    (True, False, TOOL, [("status", "OK")], 1000),                   # predicted failure, succeeded
    (True, True, TOOL, [("status", "OK")], 0),
    (None, "none", TOOL, [("status", "?")], 0),
    (False, False, REVIEW, [("correction", "do X")], 1000),          # R1: authoritative correction always flags
    (None, "none", REVIEW, [("correction", "do X")], 1000),          # R1 with unknown success
    (True, True, REVIEW, [("evaluation", "fine")], 0),               # a passing evaluation is not authority
    (False, False, TOOL, [("correction", "do X")], 1000),            # untrusted correction is not authority, but the
                                                                     # predicted failure is vague (R4), so it flags
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



R4 = [("status", "FAIL"), ("evaluation", "test_x failed as expected")]


@pytest.mark.parametrize("kw,surprise,why", [
    (dict(check="test_x", failing=("test_x",), action="after"), 0, "predicted by name"),
    (dict(check="test_x", failing=("  TEST_X ",), action="none"), 0, "predicted by name"),            # trimmed, casefolded
    (dict(check="test_x", failing=("test_x",), action="after", outcome_for_action=True), 0, "predicted by name"),
    (dict(check=None, failing=("test_x",), action="after"), 1000, "vague"),                            # adversarial: vague
    (dict(check="test_x", failing=("test_y",), action="after"), 1000, "different"),                    # adversarial: mismatch
    (dict(check="test_x", failing=("test_x", "test_y"), action="after"), 1000, "different"),           # extra failure
    (dict(check="test_x", failing=(), action="after"), 1000, "different"),                             # outcome names nothing
    (dict(check="test_x", failing=("test_x",), action="before"), 1000, "after the action"),            # adversarial: late
])
def test_r4_predicted_failures_and_anti_gaming(rw, provider, streams, kw, surprise, why):
    with rw() as s:
        o = episode(s, provider, streams["a"], success=False, sections=R4, who=REVIEW, expected_success=False, **kw)
        sc = _score(s, provider, streams["a"], o)
    assert sc.surprise == surprise and any(why in r for r in sc.reasons)


def test_r4_an_authoritative_correction_flags_even_when_the_failure_was_named_in_advance(rw, provider, streams):
    with rw() as s:
        o = episode(s, provider, streams["a"], success=False, sections=R4 + [("correction", "use test_x2 instead")],
                    expected_success=False, check="test_x", failing=("test_x",), action="after")
        assert _score(s, provider, streams["a"], o).surprise == 1000


def test_r4_a_prediction_about_another_decision_does_not_count(rw, provider, streams):
    import uuid
    from nacre.capture.record_outcome import Section, record_outcome
    from gate_kit import AG
    from nacre.capture.record_decision import record_decision
    with rw() as s:
        first = episode(s, provider, streams["a"], success=True, sections=[("status", "ok")], who=TOOL,
                        expected_success=False, check="test_x")                      # its prediction id is reused below
        pred_id = next(r["event_id"] for r in [x for x in __import__("nacre.ledger.read_stream", fromlist=["read_stream"])
                       .read_stream(s, provider, streams["a"]) if x.envelope.event_id == first.event_id][0].body["content"]["refs"]
                       if r["rel"] == "evaluates_prediction")
        d2 = record_decision(s, provider, stream_id=streams["a"], idempotency_key=str(uuid.uuid4()), decision_text="other",
                             **AG).envelope
        o = record_outcome(s, provider, stream_id=streams["a"], idempotency_key=str(uuid.uuid4()), outcome_for=d2.event_id,
                           success=False, sections=(Section("evaluation", "test_x failed"),), failing_checks=("test_x",),
                           evaluates_prediction=uuid.UUID(pred_id), **REVIEW).envelope
        sc = _score(s, provider, streams["a"], o)
    assert sc.surprise == 1000 and any("another decision" in r for r in sc.reasons)


def test_r4_a_raw_prediction_that_expected_success_never_suppresses_even_if_it_names_a_check(rw, provider, streams):
    # Defence in depth: capture refuses this shape, but the gate must not trust that every prediction came through it.
    import uuid
    from gate_kit import AG
    from nacre.capture.record_decision import record_decision
    from nacre.capture.record_outcome import Section, record_outcome
    from nacre.core.event import EventType, PayloadType
    from nacre.ledger.append_event import AppendRequest, append_event
    with rw() as s:
        d = record_decision(s, provider, stream_id=streams["a"], idempotency_key=str(uuid.uuid4()), decision_text="d", **AG).envelope
        p = append_event(s, provider, AppendRequest(
            stream_id=streams["a"], event_type=EventType.PREDICTION, payload_type=PayloadType.STRUCTURED, idempotency_key=str(uuid.uuid4()),
            content={"expected_outcome": "x", "expected_success": True, "expected_failing_check": "test_x",
                     "refs": [{"rel": "response_to", "event_id": str(d.event_id)}]}, caused_by=d.event_id, **AG)).envelope
        o = record_outcome(s, provider, stream_id=streams["a"], idempotency_key=str(uuid.uuid4()), outcome_for=d.event_id,
                           success=False, sections=(Section("evaluation", "test_x failed"),), failing_checks=("test_x",),
                           evaluates_prediction=p.event_id, **REVIEW).envelope
        assert _score(s, provider, streams["a"], o).surprise == 1000
