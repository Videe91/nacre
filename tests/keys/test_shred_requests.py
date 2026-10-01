"""Tests for keys/manage_shred_requests.py + keys/execute_due_shreds.py (D-0014 safety net, D-0004)."""
import uuid
from datetime import UTC, date, datetime, timedelta

import psycopg
import pytest

from nacre.core.event import ActorKind, EventType, PayloadType, Source
from nacre.keys.decrypt_payload import Shredded
from nacre.keys.execute_due_shreds import execute_due_shreds
from nacre.keys.keyadmin_session import KeyAdminError, keyadmin_transaction
from nacre.keys.manage_shred_requests import (ShredError, ShredKind, cancel_shred, list_shred_requests,
                                              place_legal_hold, request_shred)
from nacre.ledger.append_event import AppendRequest, Authorship, append_event
from nacre.ledger.read_stream import read_stream
from nacre.scopes.register_scope import ScopeKind, register_scope
from nacre.scopes.set_access import set_access

NOW = datetime.now(UTC)


@pytest.fixture
def world(org, provider, migrated_db):
    """An org with one project; a person and an agent have written there."""
    org_id, owner, open_ = org
    proj, person = uuid.uuid4(), uuid.uuid4()
    with open_(owner) as s:
        register_scope(s, provider, org_id=org_id, stream_id=proj, kind=ScopeKind.PROJECT, idempotency_key=str(uuid.uuid4()))
    with open_(owner) as s:
        set_access(s, provider, org_id=org_id, principal_id=owner, stream_id=proj, can_read=True, can_append=True,
                   idempotency_key=str(uuid.uuid4()))
    with open_(owner) as s:
        said = append_event(s, provider, AppendRequest(
            stream_id=proj, event_type=EventType.STATEMENT, payload_type=PayloadType.TEXT, actor_kind=ActorKind.PERSON,
            actor_id=person, source=Source.CHAT, authorship=Authorship.SCOPE_PRINCIPAL,
            idempotency_key=str(uuid.uuid4()), content="my words")).envelope
        ran = append_event(s, provider, AppendRequest(
            stream_id=proj, event_type=EventType.ACTION, payload_type=PayloadType.TEXT, actor_kind=ActorKind.AGENT,
            actor_id=uuid.uuid4(), source=Source.TOOL, idempotency_key=str(uuid.uuid4()), content="ran tests")).envelope
    ka = lambda: psycopg.connect(migrated_db["keyadmin"])  # noqa: E731
    return dict(org=org_id, owner=owner, open=open_, proj=proj, person=person, said=said, ran=ran, ka=ka)


def _request(w, provider, requester, kind, **kw):
    with w["ka"]() as c, keyadmin_transaction(c) as tx:
        return request_shred(tx, provider, org_id=w["org"], requester=requester, kind=kind,
                             idempotency_key=str(uuid.uuid4()), now=NOW, **kw)


def _state(w, provider, rid, now):
    with w["ka"]() as c, keyadmin_transaction(c) as tx:
        return next(r for r in list_shred_requests(tx, provider, w["org"], now) if r.request_id == rid).state


def _bodies(w, provider):
    with w["open"](w["owner"]) as s:
        return {e.envelope.event_id: e.body for e in read_stream(s, provider, w["proj"])}


def test_keyadmin_transaction_needs_the_keyadmin_login(migrated_db):
    from nacre.core.db import DbRole, connect
    with connect(DbRole.APP, dsn=migrated_db["app"]) as c, pytest.raises(KeyAdminError, match="nacre_keyadmin"):
        with keyadmin_transaction(c):
            pass


def test_only_admins_or_the_person_may_request(world, provider):
    with pytest.raises(ShredError, match="org admin"):
        _request(world, provider, uuid.uuid4(), ShredKind.DELETE_SCOPE, stream_id=world["proj"])
    rid = _request(world, provider, world["person"], ShredKind.ERASE_PERSON, person_id=world["person"])   # self
    assert _state(world, provider, rid, NOW) == "pending"


def test_nothing_is_destroyed_before_grace_and_cancel_stops_it(world, provider):
    rid = _request(world, provider, world["owner"], ShredKind.ERASE_PERSON, person_id=world["person"])
    assert execute_due_shreds(world["ka"](), provider, now=NOW + timedelta(days=6)) == []
    with world["ka"]() as c, keyadmin_transaction(c) as tx:
        cancel_shred(tx, provider, org_id=world["org"], requester=world["owner"], request_id=rid,
                     idempotency_key=str(uuid.uuid4()), now=NOW)
    assert execute_due_shreds(world["ka"](), provider, now=NOW + timedelta(days=8)) == []
    assert _state(world, provider, rid, NOW + timedelta(days=8)) == "cancelled"
    assert _bodies(world, provider)[world["said"].event_id]["content"] == "my words"


def test_erase_person_after_grace_shreds_only_their_events(world, provider, migrated_db):
    rid = _request(world, provider, world["owner"], ShredKind.ERASE_PERSON, person_id=world["person"])
    assert execute_due_shreds(world["ka"](), provider, now=NOW + timedelta(days=8)) == [rid]
    bodies = _bodies(world, provider)
    assert bodies[world["said"].event_id] == Shredded(world["said"].key_id)
    assert bodies[world["ran"].event_id]["content"] == "ran tests"
    markers = [b["content"] for b in bodies.values() if isinstance(b, dict) and isinstance(b["content"], dict) and b["content"].get("op") == "keys_destroyed"]
    assert markers and str(world["said"].key_id) in markers[0]["key_ids"]
    assert _state(world, provider, rid, NOW + timedelta(days=8)) == "executed"
    assert execute_due_shreds(world["ka"](), provider, now=NOW + timedelta(days=9)) == []           # runs once
    with world["ka"]() as c, keyadmin_transaction(c) as tx, pytest.raises(ShredError, match="already executed"):
        cancel_shred(tx, provider, org_id=world["org"], requester=world["owner"], request_id=rid,
                     idempotency_key=str(uuid.uuid4()), now=NOW)


def test_self_erasure_runs_automatically_unless_held_and_the_hold_expires(world, provider):
    rid = _request(world, provider, world["person"], ShredKind.ERASE_PERSON, person_id=world["person"])
    with world["ka"]() as c, keyadmin_transaction(c) as tx:
        with pytest.raises(ShredError, match="org admin"):
            place_legal_hold(tx, provider, org_id=world["org"], requester=world["person"], request_id=rid,
                             until=NOW + timedelta(days=30), reason="x", idempotency_key=str(uuid.uuid4()), now=NOW)
    with world["ka"]() as c, keyadmin_transaction(c) as tx:
        place_legal_hold(tx, provider, org_id=world["org"], requester=world["owner"], request_id=rid,
                         until=NOW + timedelta(days=30), reason="litigation notice 2026-114",
                         idempotency_key=str(uuid.uuid4()), now=NOW)
    assert execute_due_shreds(world["ka"](), provider, now=NOW + timedelta(days=8)) == []
    assert _state(world, provider, rid, NOW + timedelta(days=8)) == "held"
    assert execute_due_shreds(world["ka"](), provider, now=NOW + timedelta(days=31)) == [rid]


def test_legal_holds_must_expire_and_apply_only_to_self_erasure(world, provider):
    admin_req = _request(world, provider, world["owner"], ShredKind.ERASE_PERSON, person_id=world["person"])
    self_req = _request(world, provider, world["person"], ShredKind.ERASE_PERSON, person_id=world["person"])
    with world["ka"]() as c, keyadmin_transaction(c) as tx:
        for rid, until, match in ((admin_req, NOW + timedelta(days=5), "self-erasure"),
                                  (self_req, NOW - timedelta(days=1), "future")):
            with pytest.raises(ShredError, match=match):
                place_legal_hold(tx, provider, org_id=world["org"], requester=world["owner"], request_id=rid,
                                 until=until, reason="r", idempotency_key=str(uuid.uuid4()), now=NOW)


def test_delete_scope_destroys_the_stream_and_marks_it_deleted(world, provider, migrated_db):
    rid = _request(world, provider, world["owner"], ShredKind.DELETE_SCOPE, stream_id=world["proj"])
    assert execute_due_shreds(world["ka"](), provider, now=NOW + timedelta(days=8)) == [rid]
    assert all(isinstance(b, Shredded) for b in _bodies(world, provider).values())
    with psycopg.connect(migrated_db["admin"]) as c:
        assert c.execute("SELECT status FROM scopes.scopes WHERE stream_id = %s", (world["proj"],)).fetchone()[0] == "deleted"


def test_forget_period_destroys_only_those_months(world, provider):
    this_month = date(NOW.year, NOW.month, 1)
    rid = _request(world, provider, world["owner"], ShredKind.FORGET_PERIOD, stream_id=world["proj"],
                   months=(date(2020, 1, 1),))
    execute_due_shreds(world["ka"](), provider, now=NOW + timedelta(days=8))
    assert _bodies(world, provider)[world["ran"].event_id]["content"] == "ran tests"      # other month untouched
    rid2 = _request(world, provider, world["owner"], ShredKind.FORGET_PERIOD, stream_id=world["proj"], months=(this_month,))
    assert execute_due_shreds(world["ka"](), provider, now=NOW + timedelta(days=8)) == [rid2]
    assert isinstance(_bodies(world, provider)[world["ran"].event_id], Shredded)


def test_the_chain_still_verifies_after_execution(world, provider, migrated_db, tmp_path):
    # Gate item 4: shredding makes payloads unreadable while the chain still verifies.
    from nacre.ledger.verify_chain import verify_chain
    _request(world, provider, world["owner"], ShredKind.ERASE_PERSON, person_id=world["person"])
    execute_due_shreds(world["ka"](), provider, now=NOW + timedelta(days=8))
    with psycopg.connect(migrated_db["verifier"]) as c:
        report = verify_chain(c, tmp_path / "no-witness.jsonl", {})
    assert report.ok and report.events_checked > 0


def _epoch(migrated_db, stream):
    with psycopg.connect(migrated_db["admin"]) as c:
        row = c.execute("SELECT epoch FROM keys.shred_epochs WHERE stream_id = %s", (stream,)).fetchone()
    return row[0] if row else 0


@pytest.mark.parametrize("kind", [ShredKind.ERASE_PERSON, ShredKind.FORGET_PERIOD, ShredKind.DELETE_SCOPE])
def test_every_key_destruction_bumps_the_streams_shred_epoch_in_the_same_transaction(world, provider, migrated_db, kind):
    # D-0024 §3: warm recall caches rely on this to evict destroyed keys by the next recall.
    kw = {ShredKind.ERASE_PERSON: {"person_id": world["person"]},
          ShredKind.FORGET_PERIOD: {"stream_id": world["proj"], "months": (date(NOW.year, NOW.month, 1),)},
          ShredKind.DELETE_SCOPE: {"stream_id": world["proj"]}}[kind]
    assert _epoch(migrated_db, world["proj"]) == 0
    _request(world, provider, world["owner"], kind, **kw)
    execute_due_shreds(world["ka"](), provider, now=NOW + timedelta(days=8))
    assert _epoch(migrated_db, world["proj"]) == 1


def test_a_destruction_that_finds_no_key_does_not_bump_the_epoch(world, provider, migrated_db):
    _request(world, provider, world["owner"], ShredKind.FORGET_PERIOD, stream_id=world["proj"], months=(date(2020, 1, 1),))
    execute_due_shreds(world["ka"](), provider, now=NOW + timedelta(days=8))
    assert _epoch(migrated_db, world["proj"]) == 0

