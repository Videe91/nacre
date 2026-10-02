"""
Functionality: Rotate the root key: rewrap every live stream master key under a new root version, then destroy
  the old versions, which makes every earlier shred final.
Owns: the precondition (master rotations done first), resumable batched rewrapping, the "no row left on an old
  version" check, the root_rotated audit event per org, and destroying old versions.
Public entry: rotate_root_key(), RootRotation
Decisions: D-0004, D-0014
Assumptions: A-0008
Notes: D-0004 amendments 6-8 and D-0014: operator-only (whoever holds the root-key provider), weekly plus on demand.
  Steps:
    1. refuse if any stream still needs a master rotation (those run first, rotate_master_key);
    2. create a new root version (unless resume=True, which continues a crashed run under the current version);
    3. rewrap master keys still on an old version, in batches, one transaction per batch (resumable);
    4. verify no row references an old version;
    5. append 'root_rotated' to every org stream, carrying the operator's confirmation that the old version's
       SEPARATE backup was destroyed (a required operator step, D-0004 amendment 6);
    6. destroy the old versions in the provider.
  Step 6 comes after the audit: a crash in between leaves recorded-but-not-yet-destroyed versions, which the next
  run destroys; it never leaves an unrecorded destruction. Wrapped master keys found in older DB backups or WAL
  are wrapped under a destroyed version and can never be unwrapped again.
"""
import uuid
from dataclasses import dataclass
from uuid import UUID

import psycopg

from nacre.core.event import ActorKind, EventType, PayloadType, Source
from nacre.core.root_key_provider import RootKeyProvider, WrappedKey
from nacre.keys.keyadmin_session import keyadmin_transaction
from nacre.keys.rotate_master_key import RotationError, streams_needing_master_rotation
from nacre.ledger.append_event import AppendRequest, Authorship, append_event


@dataclass(frozen=True)
class RootRotation:
    new_version: str
    rewrapped: int
    destroyed_versions: tuple[str, ...]


def rotate_root_key(conn: psycopg.Connection, provider: RootKeyProvider, *, operator: UUID,
                    backup_destroyed_confirmation: str, batch_size: int = 100, resume: bool = False) -> RootRotation:
    if not backup_destroyed_confirmation.strip():
        raise RotationError("the operator must confirm the old version's separate backup was destroyed")
    with keyadmin_transaction(conn) as tx:
        orgs = [r[0] for r in tx.as_keyadmin().execute("SELECT stream_id FROM scopes.scopes WHERE kind = 'org'")]
        pending = [s for org in orgs for s in streams_needing_master_rotation(tx, provider, org)]
        old_versions = {v for (v,) in tx.as_keyadmin().execute("SELECT DISTINCT root_key_version FROM keys.stream_master_keys")}
    if pending:
        raise RotationError(f"run master rotations first; {len(pending)} stream(s) still need one")
    previous = provider.current_version()
    new = previous if resume else provider.create_version()
    old_versions = (old_versions | {previous}) - {new}

    rewrapped = 0
    while True:
        with keyadmin_transaction(conn) as tx:
            db = tx.as_keyadmin()
            rows = db.execute("SELECT stream_id, root_key_version, wrapped_key FROM keys.stream_master_keys "
                              "WHERE root_key_version <> %s ORDER BY stream_id LIMIT %s FOR UPDATE", (new, batch_size)).fetchall()
            for stream_id, version, wrapped in rows:
                master = provider.unwrap(WrappedKey(version, bytes(wrapped)), stream_id.bytes)
                w = provider.wrap(master, stream_id.bytes)
                db.execute("UPDATE keys.stream_master_keys SET wrapped_key = %s, root_key_version = %s WHERE stream_id = %s",
                           (w.wrapped, w.root_key_version, stream_id))
            rewrapped += len(rows)
        if len(rows) < batch_size:
            break

    with keyadmin_transaction(conn) as tx:
        left = tx.as_keyadmin().execute("SELECT count(*) FROM keys.stream_master_keys WHERE root_key_version <> %s",
                                        (new,)).fetchone()[0]
        if left:
            raise RotationError(f"{left} master key(s) still on an old root version; not destroying anything")
        for org in sorted(orgs):         # one transaction, several org streams: locks in sorted order (CURRENT F2)
            session = tx.as_app(operator, {org}, {org})
            append_event(session, provider, AppendRequest(
                stream_id=org, org_id=org, event_type=EventType.CONFIG_EVENT, payload_type=PayloadType.STRUCTURED,
                actor_kind=ActorKind.SYSTEM, actor_id=operator, source=Source.SYSTEM,
                authorship=Authorship.SCOPE_PRINCIPAL, idempotency_key=str(uuid.uuid4()),
                content={"op": "root_rotated", "to_version": new, "destroying_versions": sorted(old_versions),
                         "backup_destroyed_confirmation": backup_destroyed_confirmation}))
    destroyed = []
    for version in sorted(old_versions):
        try:
            provider.destroy_version(version)
            destroyed.append(version)
        except KeyError:
            pass                                             # already gone (an earlier, interrupted run)
    return RootRotation(new, rewrapped, tuple(destroyed))
