"""
Functionality: Repair seat: ask the model to correct the structure of the proposed propositions, and parse the final
  list.
Owns: Nacre's repair prompt (frozen by hash: PROMPT_SHA256), the request, the call through models/call_model.py, and
  parsing (the same schema as the proposer).
Public entry: repair_structure(), REPAIR_PROMPT, PROMPT_SHA256
Decisions: D-0020, D-0021, D-0022
Assumptions: A-0025
Notes: MNEXA lesson 012: a second pass that splits compound propositions and re-copies quotes improves structure.
  The FINAL list it returns is authoritative for admission, and it is re-grounded from scratch by
  sleep/admit_propositions.py, so the repair can never smuggle in text that is not in an authoritative section.
  Seed 012 alone lowered transfer, so the support-first fallback (014) stays the safety net. When the proposer
  produced nothing, there is nothing to repair and no call is made.
"""
import hashlib
import json
from uuid import UUID

from nacre.core.model_provider import CallPolicy, Message, ModelParams, ModelProvider, ModelRequest
from nacre.core.root_key_provider import RootKeyProvider
from nacre.models.call_model import DEFAULT_POLICY, call_model
from nacre.scopes.open_scoped_session import ScopedSession
from nacre.sleep.admit_propositions import Proposal
from nacre.sleep.build_evidence_bundle import EvidenceBundle
from nacre.sleep.propose_propositions import PROPOSITIONS_SCHEMA, SLEEP_MODEL, Proposed, parse_propositions

REPAIR_PROMPT = """You check and repair proposed lessons against their evidence record.

For every proposed proposition:
1. Keep it only if its rule comes from a section marked AUTHORITATIVE. Drop it otherwise.
2. Make "quote" an EXACT, character-for-character copy from that section, containing the whole rule with every
   condition, number, unit, limit, order and negation. Fix any quote that is not exact.
3. If one proposition joins rules that are independent of each other, split it into several. Steps that must all
   be done together stay one rule.
4. Make "nucleus" the shortest exact sub-quote of "quote" that names the action, and make every qualifier an exact,
   typed sub-quote of "quote".
5. Never add a rule that is not in the authoritative text, and never paraphrase.

Return the complete final list as JSON matching the schema."""
PROMPT_SHA256 = hashlib.sha256(REPAIR_PROMPT.encode()).hexdigest()


def _as_json(proposals: list[Proposal]) -> str:
    return json.dumps({"propositions": [{"section": p.section, "quote": p.quote, "nucleus": p.nucleus,
                                         "qualifiers": [{"type": t, "text": x} for t, x in p.qualifiers]}
                                        for p in proposals]}, indent=1, ensure_ascii=False)


def repair_structure(session: ScopedSession, key_provider: RootKeyProvider, model_provider: ModelProvider,
                     bundle: EvidenceBundle, initial: list[Proposal], *, run_id: UUID,
                     policy: CallPolicy = DEFAULT_POLICY) -> Proposed | None:
    """One repair call (None when there is nothing to repair). The caller commits the session afterwards."""
    if not initial:
        return None
    request = ModelRequest(provider=SLEEP_MODEL[0], model=SLEEP_MODEL[1], system=REPAIR_PROMPT,
                           messages=(Message("user", bundle.render() + "\nPROPOSED PROPOSITIONS:\n" + _as_json(initial)),),
                           purpose="sleep.repair",
                           params=ModelParams(max_tokens=1600, temperature=0.0, response_format=PROPOSITIONS_SCHEMA))
    call = call_model(session, key_provider, model_provider, request, source_event_ids=list(bundle.source_event_ids),
                      run_id=run_id, policy=policy)
    if call.response is None:
        return Proposed([], call, f"provider error: {call.error.error_class}")
    proposals, err = parse_propositions(call.response.text)
    return Proposed(proposals, call, err)
