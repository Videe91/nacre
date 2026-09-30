"""
Functionality: The lifecycle of destructive key requests: request, cancel, legal hold, and the derived state of each.
Owns: authority checks, the 7-day grace period, the request/cancel/hold ledger events in the org stream, and
  deriving every request's current state from those events.
Public entry: request_shred(), cancel_shred(), place_legal_hold(), list_shred_requests(), ShredRequest, ShredKind
Decisions: D-0004, D-0014
Assumptions: A-0012
Notes: D-0014 (owner safety net): destructive actions are RECORDED as requests; keys are destroyed only after
  GRACE (7 days), cancellable by any org admin. Self-erasure executes automatically after grace unless an org admin
  places an explicit, recorded, time-limited legal hold. All state lives in the org stream (config_event,
  structured content, op = shred_request | shred_cancel | legal_hold | shred_executed | master_rotated), so it is
  rebuildable and auditable. There is no mutable request table.
  Authority: org admin = latest grant on the org stream has can_append. Self-erasure = requester == person_id.
  Events are written AS the requester (D-0014) through keyadmin_transaction's app mode. Authority and target
  checks run as nacre_keyadmin (registry reads) in the same transaction.
"""
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from uuid import UUID

from nacre.core.event import ActorKind, EventType, PayloadType, Source
from nacre.core.root_key_provider import RootKeyProvider
from nacre.keys.keyadmin_session import KeyAdminTx
from nacre.ledger.append_event import AppendRequest, Authorship, append_event
from nacre.ledger.read_stream import read_stream

GRACE = timedelta(days=7)


class ShredKind(StrEnum):
    DELETE_SCOPE = "delete_scope"
    ERASE_PERSON = "erase_person"
    FORGET_PERIOD = "forget_period"


class ShredError(PermissionError):
    """The request is not authorised or not valid."""


@dataclass(frozen=True)
class ShredRequest:
    request_id: UUID
    kind: ShredKind
    requested_by: UUID
    execute_after: datetime
    stream_id: UUID | None
    person_id: UUID | None
    months: tuple[date, ...]
    self_erasure: bool
    state: str                       # pending | cancelled | held | due | executed
    hold_until: datetime | None


def is_org_admin(tx: KeyAdminTx, org_id: UUID, principal: UUID) -> bool:
    row = tx.as_keyadmin().execute(
        "SELECT can_append FROM scopes.scope_grants WHERE principal_id = %s AND stream_id = %s "
        "ORDER BY source_seq DESC LIMIT 1", (principal, org_id)).fetchone()
    return bool(row and row[0])


def request_shred(tx: KeyAdminTx, provider: RootKeyProvider, *, org_id: UUID, requester: UUID, kind: ShredKind,
                  idempotency_key: str, stream_id: UUID | None = None, person_id: UUID | None = None,
                  months: tuple[date, ...] = (), now: datetime | None = None) -> UUID:
    """Record a destructive request; keys are destroyed only after GRACE (execute_due_shreds)."""
    now = now or datetime.now(UTC)
    if not isinstance(kind, ShredKind):
        raise ShredError("kind must be a ShredKind")
    self_erasure = kind == ShredKind.ERASE_PERSON and person_id == requester
    if not (self_erasure or is_org_admin(tx, org_id, requester)):
        raise ShredError("only an org admin (append on the org stream), or the person themself, may request this")
    if kind in (ShredKind.DELETE_SCOPE, ShredKind.FORGET_PERIOD):
        row = tx.as_keyadmin().execute("SELECT org_id, kind, status FROM scopes.scopes WHERE stream_id = %s",
                                       (stream_id,)).fetchone()
        if row is None or row[0] != org_id or row[1] == "org" or row[2] != "active":
            raise ShredError("stream must be an active, non-org scope of this org")
    if kind == ShredKind.ERASE_PERSON and type(person_id) is not UUID:
        raise ShredError("erase_person needs person_id")
    if kind == ShredKind.FORGET_PERIOD and (not months or any(type(m) is not date or m.day != 1 for m in months)):
        raise ShredError("forget_period needs months (first day of each month)")
    request_id = uuid.uuid4()
    _record(tx, provider, org_id, requester, idempotency_key, {
        "op": "shred_request", "request_id": str(request_id), "kind": kind.value, "requested_by": str(requester),
        "stream_id": str(stream_id) if stream_id else None, "person_id": str(person_id) if person_id else None,
        "months": [m.isoformat() for m in months], "self_erasure": self_erasure,
        "execute_after": (now + GRACE).isoformat()})
    return request_id


def cancel_shred(tx: KeyAdminTx, provider: RootKeyProvider, *, org_id: UUID, requester: UUID, request_id: UUID,
                 idempotency_key: str, now: datetime | None = None) -> None:
    """Cancel a pending request (any org admin, before execution)."""
    if not is_org_admin(tx, org_id, requester):
        raise ShredError("only an org admin may cancel")
    req = _find(tx, provider, org_id, request_id, now)
    if req.state == "executed":
        raise ShredError("already executed; keys cannot be restored")
    _record(tx, provider, org_id, requester, idempotency_key,
            {"op": "shred_cancel", "request_id": str(request_id), "cancelled_by": str(requester)})


def place_legal_hold(tx: KeyAdminTx, provider: RootKeyProvider, *, org_id: UUID, requester: UUID, request_id: UUID,
                     until: datetime, reason: str, idempotency_key: str, now: datetime | None = None) -> None:
    """Hold a self-erasure until `until` (explicit, recorded, time-limited; org admin only)."""
    now = now or datetime.now(UTC)
    if not is_org_admin(tx, org_id, requester):
        raise ShredError("only an org admin may place a legal hold")
    req = _find(tx, provider, org_id, request_id, now)
    if not req.self_erasure or req.state in ("executed", "cancelled"):
        raise ShredError("legal holds apply only to pending self-erasure requests")
    if type(until) is not datetime or until.utcoffset() is None or until <= now or not reason.strip():
        raise ShredError("a legal hold needs a future, timezone-aware expiry and a reason")
    _record(tx, provider, org_id, requester, idempotency_key, {
        "op": "legal_hold", "request_id": str(request_id), "until": until.isoformat(), "placed_by": str(requester),
        "reason": reason})


def list_shred_requests(tx: KeyAdminTx, provider: RootKeyProvider, org_id: UUID,
                        now: datetime | None = None) -> list[ShredRequest]:
    """Every request of the org with its state, derived from the org stream."""
    now = now or datetime.now(UTC)
    tx.as_app(uuid.UUID(int=0), {org_id}, set())
    ops = [e.body["content"] for e in read_stream(tx.session, provider, org_id)
           if isinstance(e.body, dict) and isinstance(e.body.get("content"), dict)]
    out = []
    for c in (o for o in ops if o.get("op") == "shred_request"):
        rid = c["request_id"]
        executed = any(o.get("op") == "shred_executed" and o.get("request_id") == rid for o in ops)
        cancelled = any(o.get("op") == "shred_cancel" and o.get("request_id") == rid for o in ops)
        holds = [datetime.fromisoformat(o["until"]) for o in ops if o.get("op") == "legal_hold" and o["request_id"] == rid]
        hold_until = max(holds) if holds else None
        after = datetime.fromisoformat(c["execute_after"])
        state = ("executed" if executed else "cancelled" if cancelled
                 else "held" if hold_until and hold_until > now else "due" if now >= after else "pending")
        out.append(ShredRequest(UUID(rid), ShredKind(c["kind"]), UUID(c["requested_by"]), after,
                                UUID(c["stream_id"]) if c["stream_id"] else None,
                                UUID(c["person_id"]) if c["person_id"] else None,
                                tuple(date.fromisoformat(m) for m in c["months"]), c["self_erasure"], state, hold_until))
    return out


def _find(tx, provider, org_id, request_id, now):
    for r in list_shred_requests(tx, provider, org_id, now):
        if r.request_id == request_id:
            return r
    raise ShredError(f"no shred request {request_id} in this org")


def _record(tx: KeyAdminTx, provider, org_id, requester, idempotency_key, content) -> None:
    session = tx.as_app(requester, {org_id}, {org_id})
    append_event(session, provider, AppendRequest(
        stream_id=org_id, org_id=org_id, event_type=EventType.CONFIG_EVENT, payload_type=PayloadType.STRUCTURED,
        actor_kind=ActorKind.PERSON if content.get("self_erasure") else ActorKind.SYSTEM, actor_id=requester,
        source=Source.SYSTEM, idempotency_key=idempotency_key, content=content,
        authorship=Authorship.EXTERNAL if content.get("self_erasure") else Authorship.SCOPE_PRINCIPAL))
