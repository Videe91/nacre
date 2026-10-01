"""
Functionality: Proposer seat: ask the model for grounded propositions from one evidence bundle, and parse them.
Owns: Nacre's proposer prompt (frozen by hash: PROMPT_SHA256), the strict JSON schema, the request (pinned model,
  decoding settings), the call through models/call_model.py, and parsing into admission proposals.
Public entry: propose_propositions(), parse_propositions(), PROPOSER_PROMPT, PROPOSITIONS_SCHEMA, PROMPT_SHA256,
  SLEEP_MODEL, Proposed
Decisions: D-0020, D-0021, D-0022, D-0016
Assumptions: A-0025, A-0026
Notes: Nacre's own prompt (rebuild, not port; D-0016 amendment 2). MNEXA lessons used as design input: a failed
  decision is history (005); learn only from authoritative sections (008); copy the support exactly (007); keep the
  whole rule with its conditions in the quote and use the nucleus only as a handle (013); one proposition per
  independent rule (011). D1 decoding: temperature 0.0 and strict JSON-schema output (extraction, not generation).
  A parse failure returns NO proposals (never a guess), and the raw response stays recorded in the ledger (D-0022).
  Changing the prompt text changes PROMPT_SHA256, which a test pins, so every change is deliberate and recorded.
"""
import hashlib
import json
from dataclasses import dataclass
from uuid import UUID

from nacre.core.model_provider import CallPolicy, Message, ModelParams, ModelProvider, ModelRequest
from nacre.core.root_key_provider import RootKeyProvider
from nacre.models.call_model import DEFAULT_POLICY, ModelCall, call_model
from nacre.scopes.open_scoped_session import ScopedSession
from nacre.sleep.admit_propositions import QUALIFIER_TYPES, Proposal
from nacre.sleep.build_evidence_bundle import EvidenceBundle

SLEEP_MODEL = ("openai", "gpt-4o-mini-2024-07-18")
PROPOSER_PROMPT = """You turn one failed attempt and its evaluation into reusable operational lessons.

Rules:
1. The DECISION is what was tried, and it failed. Never treat it as true and never learn from it.
2. Learn only from sections marked AUTHORITATIVE. Every other section is context only.
3. Make one proposition per independent rule. Steps that must all be done together form ONE rule.
4. "section" is the number N of the [SN] section the rule comes from.
5. "quote" is copied EXACTLY, character for character, from that one section. It must contain the whole rule with
   every condition, number, unit, limit, order and negation it has.
6. "nucleus" is the shortest exact sub-quote of "quote" that names the rule's action. It is a retrieval handle.
7. "qualifiers" are exact sub-quotes of "quote" that restrict the rule, each typed condition, ordering, scope or
   negation. Use an empty list when there are none.
8. Never paraphrase, summarise or add anything that is not in the authoritative text. If no section is
   authoritative, return an empty list.

Return only JSON matching the schema."""
PROMPT_SHA256 = hashlib.sha256(PROPOSER_PROMPT.encode()).hexdigest()

_QUALIFIER = {"type": "object", "additionalProperties": False, "required": ["type", "text"],
              "properties": {"type": {"type": "string", "enum": sorted(QUALIFIER_TYPES)}, "text": {"type": "string"}}}
PROPOSITIONS_SCHEMA = {"type": "json_schema", "name": "propositions", "strict": True, "schema": {
    "type": "object", "additionalProperties": False, "required": ["propositions"],
    "properties": {"propositions": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["section", "quote", "nucleus", "qualifiers"],
        "properties": {"section": {"type": "integer"}, "quote": {"type": "string"}, "nucleus": {"type": "string"},
                       "qualifiers": {"type": "array", "items": _QUALIFIER}}}}}}}


@dataclass(frozen=True)
class Proposed:
    proposals: list[Proposal]
    call: ModelCall
    parse_error: str | None


def parse_propositions(text: str) -> tuple[list[Proposal], str | None]:
    """Parse a seat's JSON reply into proposals; ([], reason) when it is not the expected shape."""
    try:
        items = json.loads(text)["propositions"]
        if not isinstance(items, list):
            raise TypeError("propositions is not a list")
        return [Proposal(p["section"], p["quote"], p["nucleus"],
                         tuple((q["type"], q["text"]) for q in p["qualifiers"])) for p in items], None
    except (ValueError, KeyError, TypeError) as exc:
        return [], f"{type(exc).__name__}"


def propose_propositions(session: ScopedSession, key_provider: RootKeyProvider, model_provider: ModelProvider,
                         bundle: EvidenceBundle, *, run_id: UUID, policy: CallPolicy = DEFAULT_POLICY) -> Proposed:
    """One proposer call for `bundle`. The caller commits the session afterwards so the recording survives."""
    request = ModelRequest(provider=SLEEP_MODEL[0], model=SLEEP_MODEL[1], system=PROPOSER_PROMPT,
                           messages=(Message("user", bundle.render()),), purpose="sleep.propose",
                           params=ModelParams(max_tokens=1200, temperature=0.0, response_format=PROPOSITIONS_SCHEMA))
    call = call_model(session, key_provider, model_provider, request, source_event_ids=list(bundle.source_event_ids),
                      run_id=run_id, policy=policy)
    if call.response is None:
        return Proposed([], call, f"provider error: {call.error.error_class}")
    proposals, err = parse_propositions(call.response.text)
    return Proposed(proposals, call, err)
