"""Tests for stores/commit_episode.py: every MNEXA ADR-0009 invariant Q-1..Q-15 (Phase 2 gate item 6)."""
import inspect
import uuid

import pytest

from stores_kit import episode
from nacre.core.event import ActorKind, EventType, PayloadType, Source
from nacre.ledger.append_event import AppendRequest, Authorship, append_event
from nacre.stores import commit_episode as ce
from nacre.stores.commit_episode import SLEEP_PASS_STARTED, Anchor, EpisodeError, commit_episode
from nacre.stores.rebuild_projection import rebuild_projection
from nacre.stores.write_version import read_version_events


def _append(s, provider, stream, etype, content, **kw):
    return append_event(s, provider, AppendRequest(
        stream_id=stream, event_type=etype, payload_type=PayloadType.STRUCTURED, actor_kind=kw.pop("actor_kind", ActorKind.SYSTEM),
        actor_id=uuid.UUID(int=3), source=Source.SYSTEM, authorship=kw.pop("authorship", Authorship.SCOPE_PRINCIPAL),
        idempotency_key=str(uuid.uuid4()), content=content, **kw)).envelope


def _run(s, provider, stream):
    run = uuid.uuid4()
    _append(s, provider, stream, EventType.MEMORY_EVENT, {"op": SLEEP_PASS_STARTED, "run_id": str(run)})
    return run


def _versions(s, provider, stream, oid):
    return [v.body["content"] for v in read_version_events(s, provider, stream) if v.body["content"]["object_id"] == str(oid)]


@pytest.fixture
def ep(rw, provider, streams):
    a = streams["a"]
    with rw() as s:
        d1, o1 = episode(s, provider, a)
        d2, o2 = episode(s, provider, a)
        run = _run(s, provider, a)
    return dict(a=a, members=(d1, o1), more=(d2, o2), run=run)


def _commit(s, provider, ep, **kw):
    args = dict(stream_id=ep["a"], members=ep["members"], anchors=(), boundary_method="runtime_rule", run_id=ep["run"])
    args.update(kw)
    return commit_episode(s, provider, **args)


def test_q1_members_must_resolve(rw, provider, ep):
    with rw() as s, pytest.raises(EpisodeError, match="Q-1"):
        _commit(s, provider, ep, members=(uuid.uuid4(),))


def test_q2_q3_membership_is_the_ordered_edge_set_and_extension_is_a_new_version(rw, provider, ep):
    with rw() as s:
        oid, v1 = _commit(s, provider, ep)
        _, v2 = _commit(s, provider, ep, members=ep["members"] + ep["more"], target_object_id=oid)
        vs = _versions(s, provider, ep["a"], oid)
        edges = s.conn.execute("SELECT version, ordinal, target_event_id FROM interp.edges WHERE object_id = %s AND role = 'member' "
                               "ORDER BY version, ordinal", (oid,)).fetchall()
        assert rebuild_projection(s, provider, ep["a"]).identical
    assert (v1, v2) == (1, 2) and vs[0]["content"]["members"] == [str(m) for m in ep["members"]]
    assert [e[2] for e in edges if e[0] == 1] == list(ep["members"])             # v1 unchanged, in order
    assert [e[2] for e in edges if e[0] == 2] == list(ep["members"] + ep["more"])


def test_q4_identity_is_opaque_and_stable(rw, provider, ep):
    with rw() as s:
        oid, _ = _commit(s, provider, ep)
        oid2, _ = _commit(s, provider, ep, target_object_id=oid)
        other, _ = _commit(s, provider, ep)                                         # same members, untargeted
    assert oid2 == oid and other != oid and oid.version == 4
    assert "uuid4()" in inspect.getsource(ce) and "uuid5" not in inspect.getsource(ce)   # never derived from members/keys


def test_q5_q6_q10_anchors_are_identifiers_that_match_and_may_be_several(rw, provider, ep):
    task, cycle = uuid.uuid4(), uuid.uuid4()
    with rw() as s:
        a = _append(s, provider, ep["a"], EventType.STATEMENT, {"x": 1}, task_id=task)
        b = _append(s, provider, ep["a"], EventType.STATEMENT, {"x": 2}, cycle_id=cycle)
        _commit(s, provider, ep, members=(a.event_id, b.event_id), anchors=(Anchor("task_id", task), Anchor("cycle_id", cycle)))
        with pytest.raises(EpisodeError, match="Q-5"):
            _commit(s, provider, ep, members=(a.event_id,), anchors=(Anchor("task_id", uuid.uuid4()),))
        with pytest.raises(EpisodeError, match="Q-6"):
            _commit(s, provider, ep, members=(a.event_id,), anchors=(Anchor("time_gap", task),))


def test_q7_q8_the_runtime_commits_and_model_boundaries_cite_the_model_call(rw, provider, ep):
    with rw() as s:
        call = _append(s, provider, ep["a"], EventType.RESULT, {"kind": "model_call"}, actor_kind=ActorKind.MODEL,
                       authorship=Authorship.EXTERNAL, actor_model="gpt-4o-mini-2024-07-18")
        oid, _ = _commit(s, provider, ep, boundary_method="model_proposed", model_call_event_id=call.event_id)
        (v,) = _versions(s, provider, ep["a"], oid)
        writer = s.conn.execute("SELECT e.actor_kind FROM ledger.events e JOIN interp.versions v ON v.event_id = e.event_id "
                                "WHERE v.object_id = %s", (oid,)).fetchone()[0]
        with pytest.raises(EpisodeError, match="Q-7"):
            _commit(s, provider, ep, boundary_method="model_proposed", model_call_event_id=ep["members"][0])
        with pytest.raises(EpisodeError, match="boundary_method"):
            _commit(s, provider, ep, boundary_method=None)
    assert writer == "system" and v["content"]["boundary_method"] == "model_proposed"
    assert v["content"]["model"] == "gpt-4o-mini-2024-07-18"


def test_q9_episodes_form_only_inside_a_sleep_pass(rw, provider, ep):
    with rw() as s, pytest.raises(EpisodeError, match="Q-9"):
        _commit(s, provider, ep, run_id=uuid.uuid4())


def test_q11_q15_no_exclusivity_and_sessions_may_be_shared_or_spanned(rw, provider, ep):
    with rw() as s:
        a, _ = _commit(s, provider, ep)
        b, _ = _commit(s, provider, ep)                                             # the same records in a second episode
        c, _ = _commit(s, provider, ep, members=ep["members"] + ep["more"])        # spans two episodes' records
    assert len({a, b, c}) == 3


def test_q12_a_new_version_needs_an_explicit_lineage_target(rw, provider, ep):
    with rw() as s:
        oid, _ = _commit(s, provider, ep)
        _, v = _commit(s, provider, ep)
        assert v == 1                                                               # untargeted: a new object, not v2
        with pytest.raises(EpisodeError, match="Q-12"):
            _commit(s, provider, ep, target_object_id=uuid.uuid4())


def test_q13_q14_checks_are_admissibility_only_and_confer_no_authority(rw, provider, ep):
    with rw() as s:
        oid, _ = _commit(s, provider, ep)
        (v,) = _versions(s, provider, ep["a"], oid)
    assert v["content"]["epistemic_status"] == "interpretive_claim" and v["status"] == "active" and v["support"] is None
    src = inspect.getsource(ce.commit_episode)
    for word in ("correct", "true", "verified", "confidence"):                     # no check claims boundary correctness
        assert word not in src.lower().replace("correctness", "").replace("return", "")
