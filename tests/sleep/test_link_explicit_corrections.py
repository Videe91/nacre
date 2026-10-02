"""Tests for sleep/link_explicit_corrections.py: D-0020's explicit trigger (deterministic, no model): a trusted
`correction` naming a belief-version event -> one explicit link against that belief's head, once."""
import uuid

from sleep_kit import V1, belief, t3_episode
from nacre.capture.record_correction import record_correction
from nacre.core.event import ActorKind, Source
from nacre.ledger.append_event import Authorship
from nacre.ledger.read_stream import read_stream
from nacre.sleep.link_explicit_corrections import EXPLICIT_REASONS, link_explicit_corrections
from nacre.stores.contest_belief import contest_belief
from nacre.stores.write_version import VersionRecord, read_version_events, write_version

TEXT = "That lock timeout is wrong now: use 100 ms."


def _correct(s, kp, stream, target, *, trusted=True, text=TEXT):
    who = (dict(actor_kind=ActorKind.PERSON, source=Source.CHAT, authorship=Authorship.SCOPE_PRINCIPAL) if trusted
           else dict(actor_kind=ActorKind.TOOL, source=Source.TOOL, authorship=Authorship.EXTERNAL))
    return record_correction(s, kp, stream_id=stream, actor_id=uuid.uuid4(), idempotency_key=str(uuid.uuid4()),
                             correction_of=target, text=text, **who).envelope.event_id


def _versions(s, kp, stream, oid):
    return [v for v in read_version_events(s, kp, stream) if v.body["content"]["object_id"] == str(oid)]


def _links(world, provider):
    with world["session"]() as s:
        return [e.body["content"] for e in read_stream(s, provider, world["proj"]) if isinstance(e.body, dict)
                and (e.body.get("content") or {}).get("op") == "contradiction_proposed"]


def test_a_trusted_correction_of_a_belief_version_links_its_current_head_once(world, provider):
    a = world["proj"]
    with world["session"]() as s:
        b = belief(s, provider, a, V1)
        v1 = _versions(s, provider, a, b.object_id)[0].envelope.event_id
        belief(s, provider, a, V1)                                   # a second decision: the head is now version 2
        head = _versions(s, provider, a, b.object_id)[-1].envelope.event_id
        c = _correct(s, provider, a, v1)                             # names the OLD version
    run = uuid.uuid4()
    assert link_explicit_corrections(world["session"], provider, a, run_id=run) == {"linked": 1}
    assert link_explicit_corrections(world["session"], provider, a, run_id=run) == {}        # idempotent
    (link,) = _links(world, provider)
    assert (link["link"], link["correction_id"], link["target_event_id"], link["target_version"], link["text"]) == (
        "explicit", str(c), str(head), 2, TEXT)


def test_untrusted_corrections_and_corrections_of_other_events_make_no_link(world, provider):
    a = world["proj"]
    with world["session"]() as s:
        b = belief(s, provider, a, V1)
        _correct(s, provider, a, _versions(s, provider, a, b.object_id)[0].envelope.event_id, trusted=False)
        d, _, _ = t3_episode(s, provider, a, "Use 100 ms.")
        _correct(s, provider, a, d)                                  # a correction of a decision: not this trigger
    counts = link_explicit_corrections(world["session"], provider, a, run_id=uuid.uuid4())
    assert counts == {"untrusted": 1} and set(counts) <= EXPLICIT_REASONS and _links(world, provider) == []


def test_a_superseded_head_is_never_linked(world, provider):
    a = world["proj"]
    with world["session"]() as s:
        b = belief(s, provider, a, V1)
        (h,) = _versions(s, provider, a, b.object_id)
        c = h.body["content"]
        write_version(s, provider, a, VersionRecord(b.object_id, 2, "belief", "superseded", c["support"], c["content"]),
                      carried_from=h.envelope.event_id)              # test-only: supersession is not built here
        _correct(s, provider, a, h.envelope.event_id)
    assert link_explicit_corrections(world["session"], provider, a, run_id=uuid.uuid4()) == {"head_superseded": 1}
    assert _links(world, provider) == []


def test_explicit_links_alone_never_contest(world, provider):
    a = world["proj"]
    with world["session"]() as s:
        b = belief(s, provider, a, V1)
        v = _versions(s, provider, a, b.object_id)[0].envelope.event_id
        _correct(s, provider, a, v)
        _correct(s, provider, a, v)
    assert link_explicit_corrections(world["session"], provider, a, run_id=uuid.uuid4()) == {"linked": 2}
    with world["session"]() as s:
        assert contest_belief(s, provider, a, b.object_id).status == "active"
