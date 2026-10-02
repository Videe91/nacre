"""
Functionality: Run one interface operation (recall_context or a record_* capture) for one caller, end to end: coerce
  the JSON arguments, open the scoped session, check the claims when the caller is an authenticated principal, call
  the one public entry, and map every failure to a typed, stable error code.
Owns: the operation table (the MCP tools and the SDK share it), argument coercion, the claim derivation for capture
  writes, the result shapes, and the error-code mapping (no error echoes content or a token).
Public entry: run_operation(), Services, Caller, OperationError, OPERATIONS
Decisions: D-0026, D-0025, D-0018, D-0012
Assumptions: A-0040
Notes: D-0026 §3-5 (and amendment 3, PROPOSED: get_frame and record_statement are not built until the owner rules).
  - The actor is always the caller (§3). With a Principal, claims are checked by check_claims in the write's own
    transaction and the event is written `verified`; without one (the SDK's in-process mode) it is written `asserted`.
  - Claims for a capture write are derived from the arguments: every capture payload is structured; a correction
    event carries a correction; an outcome's sections and success give the rest (core.verified_claims). append_event
    re-checks that the verified claims describe the exact request, so a derivation slip writes nothing.
  - D1 (2026-10-02): the ContextAssembled trace of a recall is written with the caller kind's own claim from the
    D-0026 amendment 1 table (agent: chat/external/agent; person: chat/scope_principal/person; operator:
    system/scope_principal/system; service: its first source/integration_result/system). It is `asserted`:
    record_context_assembled takes no verified claims.
  - No `on_behalf_of`, refs or attachments: the capture entries accept none in Phase 3 (amendment 3, "also recorded").
  - Error codes: unauthenticated, forbidden_claim, scope_not_granted, rate_limited, invalid_request, unknown_operation,
    internal. `internal` never carries the exception text.
"""
from dataclasses import dataclass, field
from uuid import UUID

from psycopg_pool import ConnectionPool

from nacre.capture.record_action import record_action
from nacre.capture.record_correction import record_correction
from nacre.capture.record_decision import CaptureError, record_decision
from nacre.capture.record_outcome import Section, record_outcome
from nacre.capture.record_prediction import record_prediction
from nacre.capture.validate_refs import RefError
from nacre.core.embedder import Embedder
from nacre.core.event import ActorKind, Mode, Source
from nacre.core.root_key_provider import RootKeyProvider
from nacre.core.verified_claims import authority_sections
from nacre.interface.authenticate_principal import AuthError, Principal
from nacre.interface.check_claims import ClaimRejected, Claims, check_claims
from nacre.interface.render_frame import render_memory_section
from nacre.ledger.append_event import AppendError, Authorship
from nacre.recall.assemble_frame import Budget
from nacre.recall.load_index_cache import IndexCache
from nacre.recall.recall_context import RecallRequest, recall_context
from nacre.scopes.open_scoped_session import ScopeError, open_scoped_session


class OperationError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.detail = code, detail


@dataclass
class Services:
    app_pool: ConnectionPool
    key_provider: RootKeyProvider
    cache: IndexCache
    embedder: Embedder
    tau_strong_q: int
    config_version: str
    budget: Budget = field(default_factory=Budget)


@dataclass(frozen=True)
class Caller:
    principal_id: UUID
    principal: Principal | None = None          # None: in-process SDK, writes `asserted` (D-0026 §3)


_CAPTURE = {"record_decision": ("decision", record_decision), "record_prediction": ("prediction", record_prediction),
            "record_action": ("action", record_action), "record_outcome": ("outcome", record_outcome),
            "record_correction": ("correction", record_correction)}
OPERATIONS = ("recall_context",) + tuple(_CAPTURE)
_UUIDS = {"stream_id", "decided_from", "cycle_id", "task_id", "decision_id", "outcome_for", "evaluates_prediction",
          "correction_of"}
_TUPLES = {"stakes", "failing_checks"}
_TRACE_CLAIM = {"agent": ("chat", "external", "agent"), "person": ("chat", "scope_principal", "person"),
                "operator": ("system", "scope_principal", "system")}


def _coerce(args: dict) -> dict:
    out = {}
    for k, v in args.items():
        if v is None:
            if k == "success":                   # an outcome's success may be unknown (None); keep it
                out[k] = None
            continue
        if k in _UUIDS:
            v = UUID(str(v))
        elif k in _TUPLES:
            v = tuple(v)
        elif k == "sections":
            v = tuple(Section(s["role"], s["text"]) for s in v)
        elif k == "mode":
            v = Mode(v)
        out[k] = v
    return out


def _record(services: Services, caller: Caller, op: str, args: dict) -> dict:
    event_type, fn = _CAPTURE[op]
    kw = _coerce(args)
    for k in ("actor_id", "actor_kind", "source", "authorship", "verified", "session", "key_provider"):
        kw.pop(k, None)
    source, authorship, actor_kind = (args.get("source", "chat"), args.get("authorship", "external"),
                                      args.get("actor_kind", "agent"))
    content = {"success": kw.get("success"), "sections": [{"role": s.role} for s in kw.get("sections", ())]}
    corr, fail_eval = authority_sections(event_type, content)
    with services.app_pool.connection() as conn, open_scoped_session(conn, caller.principal_id) as s:
        verified = None
        if caller.principal is not None:
            verified = check_claims(s, caller.principal, Claims(kw["stream_id"], source, authorship, actor_kind, True,
                                                                corr, None, fail_eval))
        r = fn(s, services.key_provider, actor_id=caller.principal_id, actor_kind=ActorKind(actor_kind),
               source=Source(source), authorship=Authorship(authorship), verified=verified, **kw)
    env = r.envelope
    return {"event_id": str(env.event_id), "stream_id": str(env.stream_id), "commit_seq": env.commit_seq,
            "created": r.created, "trust_basis": env.trust_basis.value, "redactions": list(r.redactions)}


def _recall(services: Services, caller: Caller, args: dict) -> dict:
    kind = caller.principal.kind if caller.principal else "agent"
    if kind == "service":
        claim = (caller.principal.service_sources[0], "integration_result", "system")
    else:
        claim = _TRACE_CLAIM[kind]
    req = RecallRequest(UUID(str(args["issuing_stream"])),
                        tuple((str(level), UUID(str(sid))) for level, sid in args["scopes"]),
                        str(args["query"]), tuple(args.get("addresses") or ()))
    with services.app_pool.connection() as conn:
        res = recall_context(conn, services.key_provider, caller.principal_id, req, cache=services.cache,
                             embedder=services.embedder, tau_strong_q=services.tau_strong_q,
                             config_version=services.config_version, budget=services.budget,
                             source=Source(claim[0]), authorship=Authorship(claim[1]), actor_kind=ActorKind(claim[2]))
    return {"frame_id": res.frame.frame_id, "coverage": res.coverage, "memory": render_memory_section(res.frame.body)}


def run_operation(services: Services, caller: Caller, op: str, args: dict) -> dict:
    """The operation's JSON result, or OperationError with a stable code."""
    if op not in OPERATIONS:
        raise OperationError("unknown_operation", op if op.isidentifier() and len(op) <= 64 else "")
    try:
        return _recall(services, caller, args) if op == "recall_context" else _record(services, caller, op, args)
    except ClaimRejected as e:
        raise OperationError(e.code, str(e).split(": ", 1)[-1] if ": " in str(e) else "") from None
    except AuthError as e:
        raise OperationError("unauthenticated", e.code) from None
    except (CaptureError, RefError, AppendError) as e:
        raise OperationError("invalid_request", str(e)) from None
    except (KeyError, TypeError, ValueError) as e:
        raise OperationError("invalid_request", f"bad or missing argument ({type(e).__name__})") from None
    except ScopeError:
        raise OperationError("scope_not_granted") from None
    except Exception:                                        # noqa: BLE001 - never leak the text of an unknown failure
        raise OperationError("internal") from None
