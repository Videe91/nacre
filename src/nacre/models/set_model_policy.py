"""
Functionality: Record an org's model-provider policy, and read the policy in force.
Owns: validation of the allow-list, the `model_policy` config event in the org stream, and resolving the latest one.
Public entry: set_model_policy(), allowed_models(), PolicyError
Decisions: D-0021, D-0005
Assumptions: A-0012
Notes: D-0021 (D3, owner-approved): DEFAULT DENY. Content of a scope may go to a provider/model only if the scope's
  org has a recorded `model_policy` allowing that exact (provider, dated model). Writing needs append on the org
  stream (= org admin, enforced by RLS through the scoped session). Reading needs read on the org stream; a session
  that cannot read it gets an empty allow-list, i.e. denial, never an error that a caller might swallow.
  Each event replaces the previous allow-list entirely (latest wins); history stays in the ledger.
"""
from uuid import UUID

from nacre.core.model_provider import is_dated_pin
from nacre.core.event import ActorKind, EventType, PayloadType, Source
from nacre.core.root_key_provider import RootKeyProvider
from nacre.ledger.append_event import AppendRequest, Authorship, append_event
from nacre.ledger.read_stream import ReadError, read_stream
from nacre.scopes.open_scoped_session import ScopedSession

_PROVIDERS = ("openai", "anthropic")


class PolicyError(ValueError):
    """The policy is not valid."""


def set_model_policy(session: ScopedSession, key_provider: RootKeyProvider, *, org_id: UUID,
                     allowed: list[tuple[str, str]], idempotency_key: str) -> None:
    """Replace the org's allow-list with `allowed` = [(provider, dated model), ...]."""
    seen = set()
    for provider, model in allowed:
        if provider not in _PROVIDERS or not is_dated_pin(model):
            raise PolicyError(f"not an allowed (provider, dated model): ({provider!r}, {model!r})")
        seen.add((provider, model))
    append_event(session, key_provider, AppendRequest(
        stream_id=org_id, org_id=org_id, event_type=EventType.CONFIG_EVENT, payload_type=PayloadType.STRUCTURED,
        actor_kind=ActorKind.PERSON, actor_id=session.access.principal_id, source=Source.SYSTEM,
        authorship=Authorship.SCOPE_PRINCIPAL, idempotency_key=idempotency_key,
        content={"op": "model_policy", "allowed": [{"provider": p, "model": m} for p, m in sorted(seen)]}))


def allowed_models(session: ScopedSession, key_provider: RootKeyProvider, org_id: UUID) -> frozenset[tuple[str, str]]:
    """The org's current allow-list; empty (deny) if none is recorded or the org stream is not readable."""
    try:
        events = read_stream(session, key_provider, org_id)
    except ReadError:
        return frozenset()
    latest = None
    for e in events:
        c = e.body.get("content") if isinstance(e.body, dict) else None
        if e.envelope.event_type == EventType.CONFIG_EVENT and isinstance(c, dict) and c.get("op") == "model_policy":
            latest = c
    return frozenset((a["provider"], a["model"]) for a in latest["allowed"]) if latest else frozenset()
