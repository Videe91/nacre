"""
Functionality: Relation judge seat: ask the sleep model, once per episode, whether the episode's authoritative
  correction contradicts each candidate belief, quoting both sides, and parse the verdicts.
Owns: Nacre's judge prompt (frozen by hash: PROMPT_SHA256), the strict JSON schema, the rendered judge input (the
  episode's authoritative correction sections and the candidates' support texts), the request (pinned sleep model,
  temperature 0, fixed max tokens), the call through models/call_model.py with its sources, and parsing.
Public entry: judge_relations(), parse_verdicts(), render_judge_input(), correction_sections(), JUDGE_PROMPT,
  RELATIONS_SCHEMA, PROMPT_SHA256, PURPOSE, MAX_TOKENS, RELATIONS, Verdict, Judged
Decisions: D-0030, D-0020, D-0021, D-0022, D-0018, D-0023
Assumptions: A-0048
Notes: D-0030 owner conditions 1, 2, 5:
  - The judge ONLY proposes contradiction links: per candidate belief, `contradicts` or `unrelated`. There is no
    `same_claim` and no support attribution (the proposal's same_claim was not adopted); nothing here or downstream
    turns a verdict into support.
  - A `contradicts` verdict must quote the exact conflicting span from BOTH sides: from one of the episode's
    authoritative correction sections ([SN], N = the evidence bundle's section index) and from the belief's
    support_text ([BN], N = the candidate's 1-based rank). The verdicts are only proposals: grounding is
    sleep/ground_contradiction_links.py (deterministic; no quote, no link).
  - Runs only when there are candidates AND at least one AUTHORITATIVE section with role `correction`
    (capture/section_authority.py, computed into the bundle); otherwise None and no call.
  - Input (D1): ONLY the authoritative correction sections are shown, never tool output, untrusted or
    non-authoritative sections, and not the decision text (it is history, and nothing is quoted from it). Beliefs are
    shown by rank label only (no object ids).
  - Request: SLEEP_MODEL, temperature 0.0, MAX_TOKENS 1200 (D1: five verdicts with two quotes each), strict schema.
    Every call goes through call_model (recorded `result` event, costed per D-0021 amendment 2; D-0022). Sources
    (D-0023): the episode's decision and outcome, its action when the outcome was recorded against one, and every
    candidate head's version event. The caller (sleep/run_sleep_pass.py) commits the session right after, like the
    other seats, and replays an existing recording first (RecordedProvider).
  - A parse failure or provider error returns NO verdicts (never a guess); the raw response stays recorded.
  - Changing the prompt text changes PROMPT_SHA256, which a test pins.
"""
import hashlib
import json
from dataclasses import dataclass
from uuid import UUID

from nacre.core.model_provider import CallPolicy, Message, ModelParams, ModelProvider, ModelRequest
from nacre.core.root_key_provider import RootKeyProvider
from nacre.models.call_model import DEFAULT_POLICY, ModelCall, call_model
from nacre.scopes.open_scoped_session import ScopedSession
from nacre.sleep.build_evidence_bundle import BundleSection, EvidenceBundle
from nacre.sleep.find_contradiction_candidates import Candidate
from nacre.sleep.propose_propositions import SLEEP_MODEL

PURPOSE = "sleep.judge_relations"
MAX_TOKENS = 1200
RELATIONS = ("contradicts", "unrelated")
JUDGE_PROMPT = """You check whether a new authoritative correction contradicts existing beliefs.

You get the CORRECTIONS of one episode, each labelled [SN], and some BELIEFS, each labelled [BN].

Rules:
1. Return exactly one verdict for every belief, with "belief" set to its label, for example "B1".
2. "relation" is "contradicts" only when the correction and the belief state incompatible values or instructions
   for the same thing, so both cannot be followed at once. A different fact about the same file, a refinement, an
   addition or an agreement is "unrelated".
3. For "contradicts":
   - "section" is the number N of the [SN] correction you quote;
   - "episode_quote" is copied EXACTLY, character for character, from that correction, and contains the
     conflicting statement;
   - "belief_quote" is copied EXACTLY, character for character, from that belief, and contains the statement it
     conflicts with.
   Each quote is one contiguous piece of text that occurs only once in its source.
4. For "unrelated": "section" is -1 and both quotes are empty strings.
5. Never paraphrase. If you cannot quote both sides exactly, the answer is "unrelated".

Return only JSON matching the schema."""
PROMPT_SHA256 = hashlib.sha256(JUDGE_PROMPT.encode()).hexdigest()

RELATIONS_SCHEMA = {"type": "json_schema", "name": "relations", "strict": True, "schema": {
    "type": "object", "additionalProperties": False, "required": ["verdicts"],
    "properties": {"verdicts": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["belief", "relation", "section", "episode_quote", "belief_quote"],
        "properties": {"belief": {"type": "string"}, "relation": {"type": "string", "enum": list(RELATIONS)},
                       "section": {"type": "integer"}, "episode_quote": {"type": "string"},
                       "belief_quote": {"type": "string"}}}}}}}


@dataclass(frozen=True)
class Verdict:
    belief: object               # raw values: grounding checks every type
    relation: object
    section: object
    episode_quote: object
    belief_quote: object


@dataclass(frozen=True)
class Judged:
    verdicts: list[Verdict]
    call: ModelCall
    parse_error: str | None
    candidates: tuple[Candidate, ...]


def correction_sections(bundle: EvidenceBundle) -> list[BundleSection]:
    """The episode's AUTHORITATIVE sections with role `correction` (the only ones a link may quote)."""
    return [s for s in bundle.sections if s.authoritative and s.role == "correction"]


def render_judge_input(bundle: EvidenceBundle, candidates: list[Candidate]) -> str:
    lines = ["CORRECTIONS (authoritative):"]
    for s in correction_sections(bundle):
        lines += [f"[S{s.index}]", s.text, ""]
    lines.append("BELIEFS:")
    for n, c in enumerate(candidates, start=1):
        lines += [f"[B{n}]", c.head.support_text, ""]
    return "\n".join(lines).rstrip() + "\n"


def parse_verdicts(text: str) -> tuple[list[Verdict], str | None]:
    """Parse the judge's JSON reply; ([], reason) when it is not the expected shape."""
    try:
        items = json.loads(text)["verdicts"]
        if not isinstance(items, list):
            raise TypeError("verdicts is not a list")
        return [Verdict(v["belief"], v["relation"], v["section"], v["episode_quote"], v["belief_quote"])
                for v in items], None
    except (ValueError, KeyError, TypeError) as exc:
        return [], type(exc).__name__


def judge_relations(session: ScopedSession, key_provider: RootKeyProvider, model_provider: ModelProvider,
                    bundle: EvidenceBundle, candidates: list[Candidate], *, run_id: UUID,
                    policy: CallPolicy = DEFAULT_POLICY) -> Judged | None:
    """One judge call (None when there is nothing to judge). The caller commits the session afterwards."""
    if not candidates or not correction_sections(bundle):
        return None
    request = ModelRequest(provider=SLEEP_MODEL[0], model=SLEEP_MODEL[1], system=JUDGE_PROMPT,
                           messages=(Message("user", render_judge_input(bundle, candidates)),), purpose=PURPOSE,
                           params=ModelParams(max_tokens=MAX_TOKENS, temperature=0.0, response_format=RELATIONS_SCHEMA))
    sources = [*bundle.source_event_ids, *((bundle.action_id,) if bundle.action_id else ()),
               *(c.head.event_id for c in candidates)]
    call = call_model(session, key_provider, model_provider, request, source_event_ids=list(dict.fromkeys(sources)),
                      run_id=run_id, policy=policy)
    if call.response is None:
        return Judged([], call, f"provider error: {call.error.error_class}", tuple(candidates))
    verdicts, err = parse_verdicts(call.response.text)
    return Judged(verdicts, call, err, tuple(candidates))
