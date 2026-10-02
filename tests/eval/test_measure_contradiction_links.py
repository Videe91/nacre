"""Tests for eval/measure_contradiction_links.py: D-0030 owner condition 3 (correct-link and false-link rates, with raw
counts), computed from the ledger against synthetic T3 grading refs. Never reads a split file."""
import json
import uuid

from nacre.capture.record_decision import record_decision
from nacre.capture.record_outcome import Section, record_outcome
from nacre.core.event import ActorKind, Source
from nacre.eval.load_exp0004_set import GradingTask
from nacre.eval.measure_contradiction_links import measure_links, summarize_link_rows, t3_families, write_link_rows
from nacre.ledger.append_event import Authorship
from nacre.stores.promote_if_supported import promote_if_supported
from nacre.stores.propose_contradiction import JudgedSpans, propose_contradiction
from nacre.stores.propose_lesson import propose_lesson
from nacre.stores.write_version import read_version_events

K = lambda: str(uuid.uuid4())                                                    # noqa: E731


def _episode(s, kp, a, text):
    d = record_decision(s, kp, stream_id=a, actor_kind=ActorKind.AGENT, actor_id=uuid.UUID(int=1), source=Source.CHAT,
                        authorship=Authorship.SCOPE_PRINCIPAL, idempotency_key=K(), decision_text="keep").envelope.event_id
    o = record_outcome(s, kp, stream_id=a, actor_kind=ActorKind.SYSTEM, actor_id=uuid.UUID(int=2), source=Source.REVIEW,
                       authorship=Authorship.INTEGRATION_RESULT, idempotency_key=K(), outcome_for=d, success=False,
                       sections=(Section("status", "FAIL"), Section("correction", text))).envelope.event_id
    return d, o


def _belief(s, kp, a, text):
    d, o = _episode(s, kp, a, text)
    pid = propose_lesson(s, kp, stream_id=a, decision_id=d, outcome_id=o, section_index=1, span=(0, len(text)),
                         nucleus=text).event_id
    return o, promote_if_supported(s, kp, a, pid).object_id


def _link(s, kp, a, oid, text):
    d, o = _episode(s, kp, a, text)
    head = [v for v in read_version_events(s, kp, a) if v.body["content"]["object_id"] == str(oid)][-1]
    propose_contradiction(s, kp, stream_id=a, belief_object_id=oid, decision_id=d, text=text,
                          judged=JudgedSpans(o, 1, (0, len(text)), (0, 4), uuid.uuid4(), head.envelope.event_id))
    return o


def _task(tid, scope, type_, refs):
    return GradingTask(tid, scope, "S1", type_, False, False, {}, refs)


def test_rates_count_true_v1_v2_links_and_false_links_over_pairs_judged(session, streams, provider):
    a = streams["a"]
    with session(uuid.uuid4(), read=[a], write=[a]) as s:
        o1, b1 = _belief(s, provider, a, "Use a 1750 ms lock_timeout.")
        _, bd = _belief(s, provider, a, "Cap the queue worker at 7 attempts.")          # a distractor
        o2a = _link(s, provider, a, b1, "Migrations now set 100 ms.")                    # true conflict, linked
        o2b, _ = _episode(s, provider, a, "Use a 100 ms lock_timeout from now on.")      # true conflict, missed
        _link(s, provider, a, bd, "Use a 100 ms lock_timeout.")                          # a false link
        events = {"v1": o1, "v2a": o2a, "v2b": o2b}
        grading = [_task("t3", "sc", "T3", {"v1_event_ids": ["v1"], "v2_event_ids": ["v2a", "v2b", "absent"]}),
                   _task("t3-again", "sc", "T3", {"v1_event_ids": ["v1"], "v2_event_ids": ["v2a", "v2b"]}),
                   _task("t1", "sc", "T1", {"v1_event_ids": ["v1"], "v2_event_ids": ["v2a"]}),
                   _task("other", "sc2", "T3", {"v1_event_ids": ["v1"], "v2_event_ids": ["v2a"]})]
        fams = t3_families(grading, "sc", events)
        row = measure_links(s, provider, a, fams, pairs_judged=4)
    assert fams == [(frozenset({o1}), frozenset({o2a, o2b}))]                 # de-duplicated, scope and T3 only
    assert {k: row[k] for k in ("true_conflicts", "true_conflicts_linked", "links", "false_links", "pairs_judged")} \
        == {"true_conflicts": 2, "true_conflicts_linked": 1, "links": 2, "false_links": 1, "pairs_judged": 4}
    assert (row["correct_link_rate"], row["correct_link_fraction"]) == (0.5, "1/2")
    assert (row["false_link_rate"], row["false_link_fraction"]) == (0.25, "1/4")


def test_zero_denominators_are_none_and_rows_pool(tmp_path):
    empty = {"true_conflicts": 0, "true_conflicts_linked": 0, "links": 0, "false_links": 0, "pairs_judged": 0,
             "explicit_links": 0}
    total = summarize_link_rows([empty, empty | {"true_conflicts": 3, "true_conflicts_linked": 3, "pairs_judged": 5}])
    assert (total["correct_link_rate"], total["false_link_rate"], total["false_link_fraction"]) == (1.0, 0.0, "0/5")
    none = summarize_link_rows([])
    assert (none["correct_link_rate"], none["false_link_rate"], none["scopes"]) == (None, None, 0)
    out = write_link_rows(tmp_path, [empty])
    assert json.loads((tmp_path / "link_rows.json").read_text()) == [empty] and out["scopes"] == 1
