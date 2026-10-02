"""
Functionality: Check a write's claims against the authenticated principal, inside the write transaction: allowed
  (source, authorship, actor_kind) per principal kind, correction authority, delegations for on_behalf_of, scope grants.
Owns: the D-0026 amendment 1 claim table, the reviewer-grant and delegation lookups (live, revocable, time-limited,
  scoped), and the typed rejection codes. Over-claims are REJECTED, never downgraded.
Public entry: check_claims(), claims_of(), Claims, ClaimRejected (VerifiedClaims lives in core/verified_claims.py)
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
  - Authority (cross-checked with capture/section_authority.py): a write that WOULD be authoritative under D-0018 (a
    correction or failing-evaluation section, on a trusted event from ci/review/git or a person actor) is rejected
    unless the principal holds that authority. Agents' corrections are always rejected; their failing evaluations are
    untrusted evidence and allowed. (2026-10-02, found by the cross-check test: a person without a reviewer grant
    could otherwise record an authoritative failing evaluation.)
  - claims_of(request) derives the Claims from an AppendRequest, so the claims checked are the write's own.
"""
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from nacre.core.verified_claims import VerifiedClaims, authority_sections
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
    has_failing_evaluation: bool = False     # an outcome with success = false carrying an evaluation section


_TRUSTED = {("chat", "scope_principal"), ("git", "scope_principal"), ("review", "scope_principal"),
            ("system", "scope_principal"), ("ci", "integration_result"), ("review", "integration_result"),
            ("git", "integration_result"), ("system", "integration_result")}      # D-0012 Part A (structured results)


def _would_be_authoritative(c: Claims) -> bool:
    """D-0018 through section_authority: an authority-bearing section on a trusted event whose source is ci/review/git
    or whose actor is a person."""
    trusted = (c.source, c.authorship) in _TRUSTED and (c.authorship != "integration_result" or c.payload_structured)
    return (c.has_correction or c.has_failing_evaluation) and trusted and (
        c.source in ("ci", "review", "git") or c.actor_kind == "person")


def claims_of(request) -> Claims:
    """The Claims an AppendRequest makes (ledger.validate_append.AppendRequest)."""
    corr, fail_eval = authority_sections(request.event_type.value, request.content)
    return Claims(request.stream_id, request.source.value, request.authorship.value, request.actor_kind.value,
                  request.payload_type.value == "structured", corr, request.on_behalf_of, fail_eval)


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
    if _would_be_authoritative(claims) and not authoritative:
        raise ClaimRejected("forbidden_claim", "this write would be authoritative (D-0018) without that authority")
    c = claims
    return VerifiedClaims(principal.principal_id, c.stream_id, c.source, c.authorship, c.actor_kind,
                          c.payload_structured, c.has_correction, c.has_failing_evaluation, c.on_behalf_of,
                          authoritative)
