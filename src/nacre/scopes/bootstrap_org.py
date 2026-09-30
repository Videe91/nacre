"""
Functionality: Bootstrap a new org: its stream, its first event, and its owner's grant (admin only).
Owns: the one path that creates an org scope before anyone holds a grant on it.
Public entry: bootstrap_org()
Decisions: D-0005, D-0012
Assumptions: A-0012
Notes: D-0005: "bootstrapping the first org and its owner is a special step, recorded as the org stream's first
  event". Nobody can hold a grant on an org stream before it exists, so this runs on an ADMIN connection
  (NACRE_DSN_MIGRATOR), in ONE transaction:
    1. SET LOCAL ROLE nacre_app, with transaction-local settings admitting only the new org stream for the
       owner, and append the `bootstrap_org` config event through the normal write path;
    2. RESET ROLE, then insert the org's scope row and the owner's grant (read + append), both referencing
       that event, so the projection can be rebuilt from the ledger.
  It constructs the ScopedSession directly: the one deliberate bypass of open_scoped_session, admin only. (D1)
"""
import uuid
from uuid import UUID

import psycopg
from psycopg.pq import TransactionStatus

from nacre.core.event import ActorKind, EventType, PayloadType, Source
from nacre.core.root_key_provider import RootKeyProvider
from nacre.ledger.append_event import AppendRequest, Authorship, append_event
from nacre.scopes.open_scoped_session import ScopedSession
from nacre.scopes.resolve_access import Access


class BootstrapError(RuntimeError):
    """The org could not be bootstrapped safely."""


def bootstrap_org(admin_conn: psycopg.Connection, provider: RootKeyProvider, *, owner_principal_id: UUID,
                  idempotency_key: str, org_id: UUID | None = None) -> UUID:
    """Create an org stream whose first event records the bootstrap; grant the owner read + append."""
    if admin_conn.info.transaction_status != TransactionStatus.IDLE:
        raise BootstrapError("start outside a transaction")
    privileged = admin_conn.execute("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user").fetchone()[0]
    admin_conn.rollback()
    if not privileged:
        raise BootstrapError("bootstrap_org needs an admin connection (NACRE_DSN_MIGRATOR)")
    org = org_id or uuid.uuid4()
    with admin_conn.transaction():
        admin_conn.execute("SET LOCAL ROLE nacre_app")
        for name, value in (("nacre.principal", str(owner_principal_id)), ("nacre.read_streams", f"{{{org}}}"),
                            ("nacre.write_streams", f"{{{org}}}")):
            admin_conn.execute("SELECT set_config(%s, %s, true)", (name, value))
        session = ScopedSession(conn=admin_conn, access=Access(owner_principal_id, frozenset({org}), frozenset({org})))
        event = append_event(session, provider, AppendRequest(
            stream_id=org, org_id=org, event_type=EventType.CONFIG_EVENT, payload_type=PayloadType.STRUCTURED,
            actor_kind=ActorKind.SYSTEM, actor_id=owner_principal_id, source=Source.SYSTEM,
            authorship=Authorship.SCOPE_PRINCIPAL, idempotency_key=idempotency_key,
            content={"op": "bootstrap_org", "org_id": str(org), "owner": str(owner_principal_id)})).envelope
        admin_conn.execute("RESET ROLE")
        admin_conn.execute("INSERT INTO scopes.scopes (stream_id, kind, org_id, source_event_id) VALUES (%s, 'org', %s, %s)",
                           (org, org, event.event_id))
        admin_conn.execute("""INSERT INTO scopes.scope_grants (principal_id, stream_id, org_id, can_read, can_append,
                              source_event_id, source_seq) VALUES (%s, %s, %s, true, true, %s, %s)""",
                           (owner_principal_id, org, org, event.event_id, event.commit_seq))
    return org
