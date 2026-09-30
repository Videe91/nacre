"""
Functionality: Rotate a stream's master key after data-key shredding, so shredded data keys become final at the
  next root rotation.
Owns: deriving which streams need rotation, the single-transaction rotate (new master → rewrap every surviving data
  key → replace the old master), and the master_rotated event.
Public entry: streams_needing_master_rotation(), rotate_master_key(), run_master_rotations()
Decisions: D-0004, D-0014
Assumptions: A-0008
Notes: D-0004 amendment 7 and D-0014:
  - A stream needs rotation when the org stream has a data-key 'shred_executed' naming it (erase_person,
    forget_period) that is later, in org-stream commit order, than the stream's last 'master_rotated'. The need is
    derived from the ledger; there is no marker table. Exactly one rotation per marked stream per cycle.
  - ONE transaction per stream: generate a new master; unwrap every surviving data key with the old master and
    rewrap it with the new; UPDATE the master row to the new wrapped key (under the current root version); append
    'master_rotated' to the org stream. A crash rolls everything back, so a data key is never left wrapped only
    under a deleted master (owner condition).
  - No in-process key cache exists (get_or_create_key); if one is added it must be invalidated here.
  The old master survives only in pre-rotation backups, wrapped under a root version that the next root rotation
  destroys (rotate_root_key).
"""
import secrets
import uuid
from uuid import UUID

import psycopg

from nacre.core.event import ActorKind, EventType, PayloadType, Source
from nacre.core.root_key_provider import RootKeyProvider, WrappedKey
from nacre.keys.get_or_create_key import KEY_BYTES, unwrap_data_key, wrap_data_key
from nacre.keys.keyadmin_session import KeyAdminTx, keyadmin_transaction
from nacre.ledger.append_event import AppendRequest, Authorship, append_event
from nacre.ledger.read_stream import read_stream


class RotationError(RuntimeError):
    """The master key cannot be rotated."""


def streams_needing_master_rotation(tx: KeyAdminTx, provider: RootKeyProvider, org_id: UUID) -> list[UUID]:
    tx.as_app(uuid.UUID(int=0), {org_id}, set())
    last_shred: dict[UUID, int] = {}
    last_rotation: dict[UUID, int] = {}
    for e in read_stream(tx.session, provider, org_id):
        c = e.body.get("content") if isinstance(e.body, dict) else None
        if not isinstance(c, dict):
            continue
        if c.get("op") == "shred_executed" and c.get("kind") in ("erase_person", "forget_period"):
            for s in c["streams"]:
                last_shred[UUID(s)] = e.envelope.commit_seq
        elif c.get("op") == "master_rotated":
            last_rotation[UUID(c["stream_id"])] = e.envelope.commit_seq
    alive = {s for (s,) in tx.as_keyadmin().execute("SELECT stream_id FROM keys.stream_master_keys")}
    return sorted(s for s, seq in last_shred.items() if seq > last_rotation.get(s, 0) and s in alive)


def rotate_master_key(tx: KeyAdminTx, provider: RootKeyProvider, *, org_id: UUID, stream_id: UUID,
                      operator: UUID) -> int:
    """Rotate one stream's master key inside `tx`; returns the number of data keys rewrapped."""
    db = tx.as_keyadmin()
    row = db.execute("SELECT root_key_version, wrapped_key FROM keys.stream_master_keys WHERE stream_id = %s FOR UPDATE",
                     (stream_id,)).fetchone()
    if row is None:
        raise RotationError(f"stream {stream_id} has no master key (deleted)")
    old = provider.unwrap(WrappedKey(row[0], bytes(row[1])), stream_id.bytes)
    new = secrets.token_bytes(KEY_BYTES)
    rows = db.execute("SELECT key_id, subject_id, month, wrapped_key FROM keys.data_keys WHERE stream_id = %s FOR UPDATE",
                      (stream_id,)).fetchall()
    for key_id, subject_id, month, wrapped in rows:
        dek = unwrap_data_key(old, key_id, stream_id, subject_id, month, bytes(wrapped))
        db.execute("UPDATE keys.data_keys SET wrapped_key = %s WHERE key_id = %s",
                   (wrap_data_key(new, key_id, stream_id, subject_id, month, dek), key_id))
    replacement = provider.wrap(new, stream_id.bytes)
    db.execute("UPDATE keys.stream_master_keys SET wrapped_key = %s, root_key_version = %s WHERE stream_id = %s",
               (replacement.wrapped, replacement.root_key_version, stream_id))
    session = tx.as_app(operator, {org_id}, {org_id})
    append_event(session, provider, AppendRequest(
        stream_id=org_id, org_id=org_id, event_type=EventType.CONFIG_EVENT, payload_type=PayloadType.STRUCTURED,
        actor_kind=ActorKind.SYSTEM, actor_id=operator, source=Source.SYSTEM, authorship=Authorship.SCOPE_PRINCIPAL,
        idempotency_key=str(uuid.uuid4()),
        content={"op": "master_rotated", "stream_id": str(stream_id), "data_keys_rewrapped": len(rows)}))
    return len(rows)


def run_master_rotations(conn: psycopg.Connection, provider: RootKeyProvider, *, operator: UUID) -> list[UUID]:
    """Rotate every stream that needs it, each in its own transaction. Returns the rotated streams."""
    with keyadmin_transaction(conn) as tx:
        orgs = [r[0] for r in tx.as_keyadmin().execute("SELECT stream_id FROM scopes.scopes WHERE kind = 'org'")]
    rotated = []
    for org in orgs:
        with keyadmin_transaction(conn) as tx:
            due = streams_needing_master_rotation(tx, provider, org)
        for stream in due:
            with keyadmin_transaction(conn) as tx:
                rotate_master_key(tx, provider, org_id=org, stream_id=stream, operator=operator)
            rotated.append(stream)
    return rotated
