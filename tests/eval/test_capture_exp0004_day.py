"""Tests for eval/capture_exp0004_day.py: a whole dev scope captures through the D-0018 functions with derived trust
equal to the set's, person events land under the person's key, and unmappable shapes are refused."""
import copy
import uuid
from pathlib import Path

import pytest

from exp0004_kit import tiny_split
from phase2_kit import world
from nacre.eval.capture_exp0004_day import CaptureMappingError, IdMap, capture_day
from nacre.eval.load_exp0004_set import DEV_SHA256, load_split, split_views
from nacre.ledger.read_stream import read_stream

SET_DIR = Path(__file__).resolve().parents[2] / "tests" / "regression" / "exp0004"


@pytest.fixture
def w(org, provider):
    return world(org, provider)


def test_a_whole_dev_scope_captures_with_the_sets_trust(w, provider):
    scopes, _ = load_split(SET_DIR / "dev.json", DEV_SHA256)
    sc = scopes[0]
    ids, stream = IdMap(uuid.uuid4()), w["new_scope"]()
    for day in sc.days:
        with w["session"]() as s:
            capture_day(s, provider, stream, day, ids)
    n = sum(len(ep) for day in sc.days for ep in day)
    assert len(ids.events) == n and ids.dropped_addresses == n          # every event carries one (uncaptured) address
    with w["session"]() as s:
        evs = read_stream(s, provider, stream)
    assert len(evs) == n
    src = {e["event_id"]: e for day in sc.days for ep in day for e in ep}
    by_id = {e.envelope.event_id: e for e in evs}
    for dataset_id, eid in ids.events.items():
        env = by_id[eid].envelope
        assert (env.event_type.value, env.trust.value, env.source.value, env.actor_kind.value) == (
            src[dataset_id]["event_type"], src[dataset_id]["trust"], src[dataset_id]["source"],
            src[dataset_id]["actor_kind"])
        assert env.actor_id == ids.actor(src[dataset_id]["author"])


def test_person_events_are_under_the_persons_key_and_confidence_is_a_percentage(w, provider):
    scopes, _ = split_views(tiny_split())
    sc, ids, stream = scopes[0], IdMap(uuid.uuid4()), w["new_scope"]()
    day = copy.deepcopy(sc.days[0])
    pred = {"event_id": "x-s1-01:0099", "event_type": "prediction", "actor_kind": "agent", "author": "x-s1-01-agent",
            "source": "chat", "authorship": "scope_principal", "trust": "trusted",
            "body": {"expected_outcome": "It passes.", "expected_success": True, "predictor": "agent",
                     "confidence": 0.67}, "refs": [{"rel": "response_to", "event_id": "x-s1-01:0001"}],
            "addresses": []}
    with w["session"]() as s:
        capture_day(s, provider, stream, (day[0][:1] + (pred,) + day[0][1:],), ids)
        subject = s.conn.execute("SELECT d.subject_id FROM ledger.events e JOIN keys.data_keys d ON d.key_id = e.key_id "
                                 "WHERE e.event_id = %s", (ids.events["x-s1-01:0003"],)).fetchone()[0]
        p = next(e for e in read_stream(s, provider, stream) if e.envelope.event_id == ids.events["x-s1-01:0099"])
    assert subject == ids.actor("x-s1-01-p1")
    assert p.body["content"]["confidence_pct"] == 67


@pytest.mark.parametrize("mutate", [
    lambda ep: ep[0].update(event_type="statement"),
    lambda ep: ep[1].update(refs=[{"rel": "execution_of", "event_id": "x-s1-01:7777"}]),
    lambda ep: ep[1].update(refs=[{"rel": "continuation_of", "event_id": "x-s1-01:0001"}]),
    lambda ep: ep[2].update(trust="untrusted"),
    lambda ep: ep[0]["body"].update(decided_from="x-s1-01:0001"),
])
def test_unmappable_shapes_are_refused(w, provider, mutate):
    scopes, _ = split_views(tiny_split())
    ep = [dict(e, body=dict(e["body"])) for e in scopes[0].days[0][0]]
    mutate(ep)
    stream = w["new_scope"]()
    with pytest.raises(CaptureMappingError), w["session"]() as s:
        capture_day(s, provider, stream, (tuple(ep),), IdMap(uuid.uuid4()))


def test_the_same_event_is_never_captured_twice(w, provider):
    scopes, _ = split_views(tiny_split())
    ids, stream = IdMap(uuid.uuid4()), w["new_scope"]()
    with w["session"]() as s:
        capture_day(s, provider, stream, scopes[0].days[0][:1], ids)
    with pytest.raises(CaptureMappingError), w["session"]() as s:
        capture_day(s, provider, stream, scopes[0].days[0][:1], ids)


def test_ids_are_per_run():
    a, b = IdMap(uuid.uuid4()), IdMap(uuid.uuid4())
    assert a.stream("s") == a.stream("s") != b.stream("s") and a.actor("p") != a.stream("p")
