"""
Functionality: Check a write's claims against the authenticated principal, inside the write transaction: allowed
  (source, authorship, actor_kind) per principal kind, correction authority, delegations for on_behalf_of, scope grants.
Owns: the D-0026 amendment 1 claim table, the reviewer-grant and delegation lookups (live, revocable, time-limited,
  scoped), and the typed rejection codes. Over-claims are REJECTED, never downgraded.
Public entry: check_claims(), Claims, VerifiedClaims, ClaimRejected
Decisions: D-0026, D-0012, D-0018, D-0019, D-0023
Assumptions: A-0040
Notes: D-0026 amendment 1 (owner, 2026-10-02, D3):
  | principal              | may claim (source, authorship, actor_kind)                  | correction authority |
  | agent                  | (chat|tool, external, agent)                                | never                |
  | person                 | (chat, scope_principal, person)                             | never                |
  | person + reviewer grant| (review, scope_principal, person) on the granted stream     | yes                  |
  | service                | (one of its ci/review/git, integration_result, system), structured | yes (structured) |
  | operator               | (system, scope_principal, system)                           | never                |
  - An agent's verified events are never authoritative: they count only under the two-decision rule.
  - `on_behalf_of` is for agents only and needs a live delegation from that person covering the stream; it never
    lends the person's reviewer grant.
  - Delegations and reviewer grants are read in the caller's transaction (nacre_app sees only its own rows, 0013), so
    a revocation or expiry takes effect for the next write.
  - The stream must be in the session's write grants (scope_not_granted otherwise).
"""
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from nacre.interface.authenticate_principal import Principal
from nacre.scopes.open_scoped_session import ScopedSession


class ClaimRejected(PermissionError):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


@dataclass(frozen=True)
class Claims:
    stream_id: UUID
    source: str
    authorship: str
    actor_kind: str
    payload_structured: bool
    has_correction: bool                     # a `correction` event, or an outcome carrying a correction section
    on_behalf_of: UUID | None = None


@dataclass(frozen=True)
class VerifiedClaims:
    principal_id: UUID
    claims: Claims
    authoritative_allowed: bool


def _live(session: ScopedSession, sql: str, params: tuple, now: datetime) -> bool:
    return session.conn.execute(sql + " AND revoked_at IS NULL AND expires_at > %s LIMIT 1", params + (now,)).fetchone() \
        is not None


def check_claims(session: ScopedSession, principal: Principal, claims: Claims, *,
                 now: datetime | None = None) -> VerifiedClaims:
    """VerifiedClaims when every claim is within the principal's grants; ClaimRejected otherwise."""
    now = now or datetime.now(UTC)
    if session.access.principal_id != principal.principal_id:
        raise ClaimRejected("forbidden_claim", "the session is scoped to another principal")
    if claims.stream_id not in session.access.write_streams:
        raise ClaimRejected("scope_not_granted")
    triple = (claims.source, claims.authorship, claims.actor_kind)
    if claims.on_behalf_of is not None:
        if principal.kind != "agent":
            raise ClaimRejected("forbidden_claim", "only agents act on behalf of a person")
        if not _live(session, "SELECT 1 FROM auth.delegations WHERE agent_principal = %s AND person_principal = %s "
                     "AND %s = ANY(streams)", (principal.principal_id, claims.on_behalf_of, claims.stream_id), now):
            raise ClaimRejected("forbidden_claim", "no live delegation from that person for this stream")
    authoritative = False
    if principal.kind == "agent":
        ok = triple in {("chat", "external", "agent"), ("tool", "external", "agent")}
    elif principal.kind == "person":
        reviewer = _live(session, "SELECT 1 FROM auth.reviewer_grants WHERE person_principal = %s AND stream_id = %s",
                         (principal.principal_id, claims.stream_id), now)
        ok = triple == ("chat", "scope_principal", "person") or (
            reviewer and triple == ("review", "scope_principal", "person"))
        authoritative = reviewer and triple == ("review", "scope_principal", "person")
    elif principal.kind == "service":
        ok = (claims.source in principal.service_sources and claims.authorship == "integration_result"
              and claims.actor_kind == "system" and claims.payload_structured)
        authoritative = ok
    elif principal.kind == "operator":
        ok = triple == ("system", "scope_principal", "system")
    else:
        ok = False
    if not ok:
        raise ClaimRejected("forbidden_claim", f"{principal.kind} may not claim {triple}")
    if claims.has_correction and not authoritative:
        raise ClaimRejected("forbidden_claim", "corrections come only from reviewer-granted persons or structured "
                            "CI/integration results")
    return VerifiedClaims(principal.principal_id, claims, authoritative)
