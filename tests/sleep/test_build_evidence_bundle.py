"""Tests for sleep/build_evidence_bundle.py: authority from the envelope, rendering, errors, and resolving an outcome
to its decision (directly, or through its action: D-0020 amendment 1)."""
import dataclasses
import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from sleep_kit import record_family_via_action
from nacre.core.event import EventType
from nacre.eval.load_mnexa_family import load_mnexa_family
from nacre.keys.decrypt_payload import Shredded
from nacre.ledger.read_stream import read_stream
from nacre.sleep.build_evidence_bundle import BundleError, UnlinkedAction, build_evidence_bundle, resolve_outcome_decision

DEV = Path(__file__).resolve().parents[1] / "regression" / "exp0004" / "dev.json"     # DEV ONLY: test.json is sealed


def test_the_bundle_marks_only_the_correction_authoritative_and_labels_the_decision_as_history(world, provider, family):
    with world["session"]() as s:
        lf = load_mnexa_family(s, provider, world["proj"], family, 14)
        b = build_evidence_bundle(s, provider, world["proj"], lf.outcome_id)
    assert [x.authoritative for x in b.sections] == [False, False, True, False, False]
    text = b.render()
    assert "NOT evidence of truth" in text and "[S2] role=correction (AUTHORITATIVE)" in text
    assert "<<<" not in text and b.source_event_ids == (lf.decision_id, lf.outcome_id)


def test_only_outcomes_have_bundles(world, provider, family):
    with world["session"]() as s:
        lf = load_mnexa_family(s, provider, world["proj"], family, 14)
        with pytest.raises(BundleError):
            build_evidence_bundle(s, provider, world["proj"], lf.decision_id)
        with pytest.raises(BundleError):
            build_evidence_bundle(s, provider, world["proj"], uuid.uuid4())


# ---- D-0020 amendment 1: outcomes recorded against an action ----

def _index(world, provider, stream=None):
    with world["session"]() as s:
        return {e.envelope.event_id: e for e in read_stream(s, provider, stream or world["proj"])}


def test_an_outcome_for_an_action_resolves_to_the_actions_decision_and_bundles_like_a_direct_one(world, provider, family):
    direct = world["new_stream"]()
    with world["session"]() as s:
        d, a, o = record_family_via_action(s, provider, world["proj"], family)
        lf = load_mnexa_family(s, provider, direct, family, 14)
        b = build_evidence_bundle(s, provider, world["proj"], o)
        ref = build_evidence_bundle(s, provider, direct, lf.outcome_id)
    assert resolve_outcome_decision(_index(world, provider)[o], _index(world, provider)).decision_id == d
    assert (b.decision_id, b.action_id, b.outcome_id, b.source_event_ids) == (d, a, o, (d, o))
    assert ref.action_id is None and b.render() == ref.render() and b.sections == ref.sections


@pytest.mark.parametrize("case", ["none", "non_decision", "other_stream"])
def test_an_action_without_a_readable_decision_link_is_unlinked(world, provider, family, case):
    with world["session"]() as s:
        _, linked_action, _ = record_family_via_action(s, provider, world["proj"], family)
    target = {"none": "none", "non_decision": linked_action}.get(case)
    if case == "other_stream":
        other = world["new_stream"]()
        with world["session"]() as s:
            target, _, _ = record_family_via_action(s, provider, other, family)    # a real decision, another stream
    with world["session"]() as s:
        _, _, o = record_family_via_action(s, provider, world["proj"], family, link=target)
    index = _index(world, provider)
    with pytest.raises(UnlinkedAction):
        resolve_outcome_decision(index[o], index)
    with pytest.raises(UnlinkedAction):
        build_evidence_bundle(None, provider, world["proj"], o, index)


def test_an_unreadable_action_or_an_erased_decision_is_unlinked_and_a_direct_erased_decision_is_not(world, provider, family):
    with world["session"]() as s:
        d, a, o = record_family_via_action(s, provider, world["proj"], family)
        lf = load_mnexa_family(s, provider, world["proj"], family, 14)
    index = _index(world, provider)
    shred = lambda i: {**index, i: dataclasses.replace(index[i], body=Shredded(index[i].envelope.key_id))}  # noqa: E731
    with pytest.raises(UnlinkedAction):
        resolve_outcome_decision(index[o], shred(a))
    with pytest.raises(UnlinkedAction):
        build_evidence_bundle(None, provider, world["proj"], o, shred(d))
    with pytest.raises(BundleError) as direct:
        build_evidence_bundle(None, provider, world["proj"], lf.outcome_id, shred(lf.decision_id))
    assert not isinstance(direct.value, UnlinkedAction)                     # only actions are counted as unlinked


def test_nothing_is_inferred_from_the_decision_right_before_the_action(world, provider, family):
    with world["session"]() as s:
        d, a, o = record_family_via_action(s, provider, world["proj"], family, link="none")
    index = _index(world, provider)
    order = sorted(index.values(), key=lambda e: e.envelope.commit_seq)
    ids = [e.envelope.event_id for e in order]
    assert ids[ids.index(a) - 1] == d                                       # the decision is the action's neighbour
    with pytest.raises(UnlinkedAction):
        resolve_outcome_decision(index[o], index)


def test_every_outcome_of_the_frozen_dev_split_resolves_through_its_action_to_a_decision():
    """Shape check on the frozen EXP-0004 DEV split (never test.json). Prints counts only, never task text."""
    data = json.loads(DEV.read_text())
    assert data["split"] == "dev"
    resolved = 0
    for scope in data["scopes"]:
        ids = {}
        index = {}
        for day in scope["days"]:
            for ep in day["episodes"]:
                for ev in ep["events"]:
                    eid = ids.setdefault(ev["event_id"], uuid.uuid4())
                    refs = [{"rel": r["rel"], "event_id": str(ids.setdefault(r["event_id"], uuid.uuid4()))}
                            for r in ev["refs"]]
                    index[eid] = SimpleNamespace(envelope=SimpleNamespace(event_id=eid, event_type=EventType(ev["event_type"])),
                                                 body={"content": {**ev["body"], "refs": refs}})
        for e in index.values():
            if e.envelope.event_type == EventType.OUTCOME:
                r = resolve_outcome_decision(e, index)
                assert r.action_id is not None and index[r.decision_id].envelope.event_type == EventType.DECISION
                resolved += 1
    print(f"dev split: {resolved} outcomes resolved through their action to a decision")
    assert resolved == 2149
