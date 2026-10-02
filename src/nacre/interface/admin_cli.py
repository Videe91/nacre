"""
Functionality: Administer principals, tokens, delegations and reviewer grants: every change as nacre_principal_admin
  AND a config_event in the org stream written as the operator, in ONE transaction; plus the command-line entry.
Owns: the admin operations, their recorded config_event payloads (never a secret), token issuance (shown once), and
  the argparse CLI.
Public entry: AdminOps, main()
Decisions: D-0026, D-0014, D-0005
Assumptions: A-0039, A-0040
Notes: D-0026 §1-2 and amendments 1-2 (owner, 2026-10-02).
  - The connection must be logged in as nacre_principal_admin (0013). Inside one transaction the config_event is
    appended first (SET LOCAL ROLE nacre_app with transaction-local settings for the org stream, written as the
    operator), then the auth row is written as nacre_principal_admin carrying that event's id. Both commit or neither.
  - Delegations and reviewer grants are explicit, recorded, time-limited (<= 90 days, enforced in the schema),
    revocable and scoped (owner decisions).
  - A token is returned ONCE by issue_token and never logged or recorded; its config_event holds only the token id.
"""
import argparse
import sys
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID

import psycopg
from psycopg.pq import TransactionStatus

from nacre.core.event import ActorKind, EventType, PayloadType, Source
from nacre.core.root_key_provider import RootKeyProvider
from nacre.interface.token_format import new_token, token_mac
from nacre.ledger.append_event import AppendRequest, Authorship, append_event
from nacre.scopes.open_scoped_session import ScopedSession
from nacre.scopes.resolve_access import Access


class AdminError(RuntimeError):
    """The admin operation cannot run safely, or its input is invalid."""


class AdminOps:
    def __init__(self, conn: psycopg.Connection, key_provider: RootKeyProvider, token_key: bytes, operator_id: UUID,
                 org_id: UUID):
        user = conn.execute("SELECT session_user").fetchone()[0]
        conn.rollback()
        if user != "nacre_principal_admin":
            raise AdminError(f"principal administration needs a nacre_principal_admin login, not {user}")
        self.conn, self.kp, self.key, self.operator, self.org = conn, key_provider, token_key, operator_id, org_id

    def _tx(self, op: dict, write) -> UUID:
        if self.conn.info.transaction_status != TransactionStatus.IDLE:
            raise AdminError("start outside a transaction")
        with self.conn.transaction():
            self.conn.execute("SET LOCAL ROLE nacre_app")
            for name in ("nacre.read_streams", "nacre.write_streams"):
                self.conn.execute("SELECT set_config(%s, %s, true)", (name, "{" + str(self.org) + "}"))
            self.conn.execute("SELECT set_config('nacre.principal', %s, true)", (str(self.operator),))
            s = ScopedSession(conn=self.conn, access=Access(self.operator, frozenset({self.org}), frozenset({self.org})))
            env = append_event(s, self.kp, AppendRequest(
                stream_id=self.org, event_type=EventType.CONFIG_EVENT, payload_type=PayloadType.STRUCTURED,
                actor_kind=ActorKind.SYSTEM, actor_id=self.operator, source=Source.SYSTEM,
                authorship=Authorship.SCOPE_PRINCIPAL, idempotency_key=str(uuid.uuid4()), content=op)).envelope
            self.conn.execute("RESET ROLE")
            write(env.event_id)
        return env.event_id

    def create_principal(self, kind: str, display_name: str, service_sources: Sequence[str] = ()) -> UUID:
        pid = uuid.uuid4()
        self._tx({"op": "principal_created", "principal_id": str(pid), "kind": kind,
                  "service_sources": sorted(service_sources)},
                 lambda ev: self.conn.execute(
                     "INSERT INTO auth.principals (principal_id, org_id, kind, display_name, service_sources, "
                     "config_event_id) VALUES (%s, %s, %s, %s, %s, %s)",
                     (pid, self.org, kind, display_name, sorted(service_sources), ev)))
        return pid

    def disable_principal(self, principal_id: UUID, reason: str) -> None:
        self._tx({"op": "principal_disabled", "principal_id": str(principal_id), "reason": reason},
                 lambda ev: self._one("UPDATE auth.principals SET disabled_at = now() WHERE principal_id = %s "
                                      "AND disabled_at IS NULL", (principal_id,)))

    def issue_token(self, principal_id: UUID, days: int = 90) -> str:
        """The token string, returned ONCE. Only its keyed hash is stored; the event records only its id."""
        token, tid, secret = new_token()
        expires = datetime.now(UTC) + timedelta(days=days)
        self._tx({"op": "token_issued", "principal_id": str(principal_id), "token_id": str(tid),
                  "expires_at": expires.isoformat()},
                 lambda ev: self.conn.execute(
                     "INSERT INTO auth.tokens (token_id, principal_id, token_mac, expires_at) VALUES (%s, %s, %s, %s)",
                     (tid, principal_id, token_mac(self.key, secret), expires)))
        return token

    def revoke_token(self, token_id: UUID) -> None:
        self._tx({"op": "token_revoked", "token_id": str(token_id)},
                 lambda ev: self._one("UPDATE auth.tokens SET revoked_at = now() WHERE token_id = %s "
                                      "AND revoked_at IS NULL", (token_id,)))

    def grant_delegation(self, agent: UUID, person: UUID, streams: Sequence[UUID], days: int) -> UUID:
        did = uuid.uuid4()
        expires = datetime.now(UTC) + timedelta(days=days)
        self._tx({"op": "delegation_granted", "delegation_id": str(did), "agent": str(agent), "person": str(person),
                  "streams": sorted(map(str, streams)), "expires_at": expires.isoformat()},
                 lambda ev: self.conn.execute(
                     "INSERT INTO auth.delegations (delegation_id, agent_principal, person_principal, streams, "
                     "expires_at, config_event_id) VALUES (%s, %s, %s, %s, %s, %s)",
                     (did, agent, person, list(streams), expires, ev)))
        return did

    def revoke_delegation(self, delegation_id: UUID) -> None:
        self._tx({"op": "delegation_revoked", "delegation_id": str(delegation_id)},
                 lambda ev: self._one("UPDATE auth.delegations SET revoked_at = now() WHERE delegation_id = %s "
                                      "AND revoked_at IS NULL", (delegation_id,)))

    def grant_reviewer(self, person: UUID, stream: UUID, days: int) -> UUID:
        gid = uuid.uuid4()
        expires = datetime.now(UTC) + timedelta(days=days)
        self._tx({"op": "reviewer_granted", "grant_id": str(gid), "person": str(person), "stream": str(stream),
                  "expires_at": expires.isoformat()},
                 lambda ev: self.conn.execute(
                     "INSERT INTO auth.reviewer_grants (grant_id, person_principal, stream_id, expires_at, "
                     "config_event_id) VALUES (%s, %s, %s, %s, %s)", (gid, person, stream, expires, ev)))
        return gid

    def revoke_reviewer(self, grant_id: UUID) -> None:
        self._tx({"op": "reviewer_revoked", "grant_id": str(grant_id)},
                 lambda ev: self._one("UPDATE auth.reviewer_grants SET revoked_at = now() WHERE grant_id = %s "
                                      "AND revoked_at IS NULL", (grant_id,)))

    def _one(self, sql: str, params: tuple) -> None:
        if self.conn.execute(sql, params).rowcount != 1:
            raise AdminError("no matching active row")


def main(argv: Sequence[str] | None = None) -> int:
    """`python -m nacre.interface.admin_cli <command> ...` (connection and keys from NACRE_* environment variables)."""
    from nacre.core.db import DbRole, connect
    from nacre.interface.token_format import load_token_key
    from nacre.keys.local_file_root_key import LocalFileRootKeyProvider
    import os
    from pathlib import Path
    ap = argparse.ArgumentParser(prog="nacre-admin")
    ap.add_argument("--operator", type=UUID, required=True)
    ap.add_argument("--org", type=UUID, required=True)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("create-principal"); p.add_argument("kind"); p.add_argument("name"); p.add_argument("--sources", nargs="*", default=[])
    p = sub.add_parser("issue-token"); p.add_argument("principal", type=UUID); p.add_argument("--days", type=int, default=90)
    p = sub.add_parser("revoke-token"); p.add_argument("token", type=UUID)
    p = sub.add_parser("disable-principal"); p.add_argument("principal", type=UUID); p.add_argument("reason")
    p = sub.add_parser("grant-delegation"); p.add_argument("agent", type=UUID); p.add_argument("person", type=UUID); p.add_argument("--streams", type=UUID, nargs="+", required=True); p.add_argument("--days", type=int, required=True)
    p = sub.add_parser("revoke-delegation"); p.add_argument("delegation", type=UUID)
    p = sub.add_parser("grant-reviewer"); p.add_argument("person", type=UUID); p.add_argument("stream", type=UUID); p.add_argument("--days", type=int, required=True)
    p = sub.add_parser("revoke-reviewer"); p.add_argument("grant", type=UUID)
    a = ap.parse_args(argv)
    with connect(DbRole.PRINCIPAL_ADMIN) as conn:
        ops = AdminOps(conn, LocalFileRootKeyProvider(Path(os.environ["NACRE_ROOT_KEY_DIR"])), load_token_key(), a.operator, a.org)
        out = {"create-principal": lambda: ops.create_principal(a.kind, a.name, a.sources),
               "issue-token": lambda: ops.issue_token(a.principal, a.days),
               "revoke-token": lambda: ops.revoke_token(a.token),
               "disable-principal": lambda: ops.disable_principal(a.principal, a.reason),
               "grant-delegation": lambda: ops.grant_delegation(a.agent, a.person, a.streams, a.days),
               "revoke-delegation": lambda: ops.revoke_delegation(a.delegation),
               "grant-reviewer": lambda: ops.grant_reviewer(a.person, a.stream, a.days),
               "revoke-reviewer": lambda: ops.revoke_reviewer(a.grant)}[a.cmd]()
    if out is not None:
        print(out)                    # issue-token prints the token ONCE, to the operator's terminal only
    return 0


if __name__ == "__main__":
    sys.exit(main())
