"""
Functionality: The EXP-0004 transfer instrument, identical for arms C / V / N: build the fixed prompt around one memory
  section and one task, call the pinned model through call_model, and parse the structured reply.
Owns: the instrument text and its sha256 pin, the transfer model and decoding parameters, the prompt assembly, the
  reply schema and parser, and the per-arm request purpose.
Public entry: transfer(), transfer_prompt(), parse_reply(), Reply, Transfer, BASE_INSTRUCTION, INSTRUMENT_TEMPLATE,
  INSTRUMENT_SHA256, INSTRUMENT_APPROVED, instrument_sha256(), TRANSFER_MODEL, TRANSFER_PARAMS, REPLY_SCHEMA, NO_MEMORY
Decisions: D-0021, D-0022, D-0025, D-0016
Assumptions: A-0025
Notes: EVALUATION HARNESS ONLY (EXP-0004 "Arms"). Fixed by the pre-registration: one model for every seat and arm
  (gpt-4o-mini-2024-07-18); a fixed prompt with a memory section and the task; the reply {"answer": string|null,
  "ask": boolean}; BASE_INSTRUCTION verbatim; the text frozen by sha256 before the run; C's memory section "(none)".
  - DRAFT, NOT OWNER-APPROVED (INSTRUMENT_APPROVED = False): the rest of the prompt wording, the order (memory, then
    task), TRANSFER_PARAMS (temperature 0, max 400 output tokens, strict JSON-schema output, as the sleep seats) and the
    parser's strictness are open in the EXP-0004 text. The runner refuses a live run until the owner approves them and
    flips the flag; the sha256 then freezes exactly what was approved.
  - Assembly splits the template on its two markers, so memory text containing a marker or braces cannot alter the
    rest of the prompt; everything except the memory section is byte-identical across arms (audited).
  - Parser: strict JSON object with exactly the keys answer (string or null) and ask (boolean). Anything else is
    `parsed = False` (answer None, ask False); the raw text is kept for the safety regexes (grade_exp0004).
  - Every call is recorded by call_model with the canonical request (all decoding parameters, unset ones as null)
    and its cost (D-0021 amendment 1).
"""
import hashlib
import json
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from nacre.core.model_provider import CallPolicy, Message, ModelParams, ModelProvider, ModelRequest
from nacre.core.root_key_provider import RootKeyProvider
from nacre.models.call_model import DEFAULT_POLICY, call_model
from nacre.scopes.open_scoped_session import ScopedSession

BASE_INSTRUCTION = "If the memory does not tell you and the task depends on it, set ask = true."
NO_MEMORY = "(none)"
_MEMORY, _TASK = "<<MEMORY>>", "<<TASK>>"
INSTRUMENT_TEMPLATE = (
    "You are an agent working on one task in a project. Below is your memory of the project, then the task.\n\n"
    "Memory:\n" + _MEMORY + "\n\n"
    "Task:\n" + _TASK + "\n\n"
    + BASE_INSTRUCTION + "\n"
    'Reply with a JSON object only: {"answer": string or null, "ask": true or false}. '
    'Put your answer to the task in "answer"; if you ask instead, set "answer" to null.')
INSTRUMENT_SHA256 = "ebbcaf3ca1e4992202f7b7ee7105c398a43c7d7c9a3c20578517f0c859f4529b"
INSTRUMENT_APPROVED = False
TRANSFER_MODEL = ("openai", "gpt-4o-mini-2024-07-18")
REPLY_SCHEMA = {"type": "json_schema", "name": "transfer_reply", "strict": True,
                "schema": {"type": "object", "additionalProperties": False, "required": ["answer", "ask"],
                           "properties": {"answer": {"type": ["string", "null"]}, "ask": {"type": "boolean"}}}}
TRANSFER_PARAMS = ModelParams(max_tokens=400, temperature=0.0, response_format=REPLY_SCHEMA)


@dataclass(frozen=True)
class Reply:
    answer: str | None
    ask: bool
    parsed: bool
    raw: str


@dataclass(frozen=True)
class Transfer:
    reply: Reply
    prompt: str
    request_sha256: str
    cost_usd: Decimal
    input_tokens: int
    output_tokens: int


def transfer_prompt(task: str, memory_section: str) -> str:
    """The instrument with this memory section and task (split on the markers, never formatted)."""
    before, rest = INSTRUMENT_TEMPLATE.split(_MEMORY)
    middle, after = rest.split(_TASK)
    return before + (memory_section.rstrip("\n") or NO_MEMORY) + middle + task + after


def parse_reply(text: str) -> Reply:
    try:
        obj = json.loads(text)
    except (ValueError, TypeError):
        return Reply(None, False, False, text)
    if (not isinstance(obj, dict) or set(obj) != {"answer", "ask"} or type(obj["ask"]) is not bool
            or not (obj["answer"] is None or isinstance(obj["answer"], str))):
        return Reply(None, False, False, text)
    return Reply(obj["answer"], obj["ask"], True, text)


def transfer(open_session: Callable[[], AbstractContextManager[ScopedSession]], key_provider: RootKeyProvider,
             provider: ModelProvider, *, arm: str, task: str, memory_section: str, sources: list[UUID], run_id: UUID,
             policy: CallPolicy = DEFAULT_POLICY) -> Transfer:
    """One transfer call for `arm` (C, V or N); `sources` are the stream events the memory section came from."""
    prompt = transfer_prompt(task, memory_section)
    req = ModelRequest(provider=TRANSFER_MODEL[0], model=TRANSFER_MODEL[1], messages=(Message("user", prompt),),
                       params=TRANSFER_PARAMS, purpose=f"eval.exp0004.transfer.{arm}")
    with open_session() as s:
        call = call_model(s, key_provider, provider, req, source_event_ids=sources, run_id=run_id, policy=policy)
    call.raise_for_error()                                   # after the commit: failed attempts stay recorded
    u = call.response.usage
    return Transfer(parse_reply(call.response.text), prompt, call.request_sha256, call.cost_usd, u.input_tokens,
                    u.output_tokens)


def instrument_sha256() -> str:
    return hashlib.sha256(INSTRUMENT_TEMPLATE.encode()).hexdigest()
