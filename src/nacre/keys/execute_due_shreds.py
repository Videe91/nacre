"""
Functionality: Execute every due destructive request: destroy the keys and record it, atomically, per request.
Owns: what each kind destroys, the scope status change on deletion, and the execution and deletion-marker events.
Public entry: execute_due_shreds()
Decisions: D-0004, D-0014
Assumptions: A-0008
Notes: A request is due when its grace period has passed, it is not cancelled, and it is not under an active legal
  hold (manage_shred_requests.list_shred_requests). One keyadmin transaction per request, so the destruction and
  its events commit together or not at all:
    delete_scope  -> DELETE the stream's master key (its data keys cascade); scope status = 'deleted';
                     'shred_executed' in the org stream (the stream's own keys are gone, D-0004).
    erase_person  -> DELETE every data key of the person in every stream of the org; a deletion_marker in each
                     affected stream (encrypted under that stream's CURRENT system key, never a destroyed key)
                     + 'shred_executed' in the org stream.
    forget_period -> DELETE the stream's data keys for the given months; deletion_marker + 'shred_executed'.
  Destroyed key rows still survive in backups and WAL. Finality comes from master rotation (for data-key kinds)
  and then root rotation (D-0004 amendments 6-7; rotate_master_key, rotate_root_key). Events are written AS
  the original requester (D-0014).
"""
import uuid
from datetime import UTC, datetime
from uuid import UUID

import psycopg

from nacre.core.event import ActorKind, EventType, PayloadType, Source
from nacre.core.root_key_provider import RootKeyProvider
from nacre.keys.keyadmin_session import keyadmin_transaction
from nacre.keys.manage_shred_requests import ShredKind, ShredRequest, list_shred_requests
from nacre.ledger.append_event import AppendRequest, Authorship, append_event


def execute_due_shreds(conn: psycopg.Connection, provider: RootKeyProvider, *, now: datetime | None = None) -> list[UUID]:
    """Run every due request in every org; returns the executed request ids."""
    now = now or datetime.now(UTC)
    with keyadmin_transaction(conn) as tx:
        orgs = [r[0] for r in tx.as_keyadmin().execute("SELECT stream_id FROM scopes.scopes WHERE kind = 'org'")]
    executed = []
    for org in orgs:
        with keyadmin_transaction(conn) as tx:
            due = [r for r in list_shred_requests(tx, provider, org, now) if r.state == "due"]
        for req in due:
            with keyadmin_transaction(conn) as tx:
                _execute(tx, provider, org, req)
            executed.append(req.request_id)
    return executed


def _execute(tx, provider, org: UUID, req: ShredRequest) -> None:
    db = tx.as_keyadmin()
    markers: dict[UUID, list[str]] = {}
    if req.kind == ShredKind.DELETE_SCOPE:
        key_ids = [str(k) for (k,) in db.execute("SELECT key_id FROM keys.data_keys WHERE stream_id = %s", (req.stream_id,))]
        db.execute("DELETE FROM keys.stream_master_keys WHERE stream_id = %s", (req.stream_id,))
        db.execute("UPDATE scopes.scopes SET status = 'deleted' WHERE stream_id = %s", (req.stream_id,))
        streams = [req.stream_id]
    else:
        if req.kind == ShredKind.ERASE_PERSON:
            rows = db.execute("""DELETE FROM keys.data_keys d USING scopes.scopes s
                                 WHERE d.stream_id = s.stream_id AND s.org_id = %s AND d.subject_id = %s
                                 RETURNING d.key_id, d.stream_id""", (org, req.person_id)).fetchall()
        else:
            rows = db.execute("DELETE FROM keys.data_keys WHERE stream_id = %s AND month = ANY(%s) "
                              "RETURNING key_id, stream_id", (req.stream_id, list(req.months))).fetchall()
        for key_id, stream in rows:
            markers.setdefault(stream, []).append(str(key_id))
        key_ids = [k for ks in markers.values() for k in ks]
        streams = sorted(markers)
    alive = {s for (s,) in db.execute("SELECT stream_id FROM keys.stream_master_keys WHERE stream_id = ANY(%s)",
                                      (list(markers),))}
    session = tx.as_app(req.requested_by, {org, *alive}, {org, *alive})
    for stream in sorted(alive):
        _event(session, provider, stream, org, req.requested_by, EventType.DELETION_MARKER,
               {"op": "keys_destroyed", "request_id": str(req.request_id), "key_ids": markers[stream]})
    _event(session, provider, org, org, req.requested_by, EventType.CONFIG_EVENT,
           {"op": "shred_executed", "request_id": str(req.request_id), "kind": req.kind.value,
            "streams": [str(s) for s in streams], "key_ids": key_ids})


def _event(session, provider, stream, org, principal, event_type, content):
    append_event(session, provider, AppendRequest(
        stream_id=stream, org_id=org, event_type=event_type, payload_type=PayloadType.STRUCTURED,
        actor_kind=ActorKind.SYSTEM, actor_id=principal, source=Source.SYSTEM, authorship=Authorship.SCOPE_PRINCIPAL,
        idempotency_key=str(uuid.uuid4()), content=content))
