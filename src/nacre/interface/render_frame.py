"""
Functionality: Render a frozen ContextFrame for a model: one deterministic memory section, identical for every
  provider, plus the provider-neutral request (system + task) the adapters map to their own APIs.
Owns: the memory-section text, the contested wording (never phrased as fact), the coverage instruction, and the
  rule that rendering never adds, drops or reorders items.
Public entry: render_memory_section(), render_request(), frame_id_of(), RENDER_VERSION
Decisions: D-0025, D-0028, D-0021, D-0022, D-0008
Assumptions: none
Notes: D-0025 §7 and amendment 1 (owner, 2026-10-02).
  - A pure function of the frame body: same frame -> byte-identical memory section, whatever the provider (D-0028
    cross-provider test). Provider differences live only in the adapters (models/*_provider.py).
  - Items appear in frame order, numbered; nothing is added, dropped or reordered.
  - Uncontested items: "<n>. <text>" with qualifiers and the scope level.
  - Contested items are never phrased as fact: "<n>. CONTESTED, not established: <text>. Contradicting evidence:
    <text>." (amendment 1).
  - Coverage: on weak or none, the section ends with an instruction to ask rather than guess (D-0025 §6).
  - Nothing here reads the database or keys; reasoning prompts never flow back into the frame.
  - D-0022 amendment 1 / D-0028 §3: the request names the frame it was rendered from (`frame_id`), so every
    provider's recorded call of one recall names the same frame. frame_id_of() is the frame id rule of
    recall/assemble_frame.py (sha256 of the body's deterministic CBOR, D-0025, D-0008), recomputed from the body so a
    caller cannot pair a body with another frame's id; a test pins it to assemble_frame's Frame.frame_id. frame_id is
    metadata: it never changes the prompt bytes.
"""
import hashlib

from nacre.core.encode_cbor import encode_cbor
from nacre.core.model_provider import Message, ModelParams, ModelRequest

RENDER_VERSION = 1
_ASK = ("Memory coverage for this task is {coverage}. If the memory does not settle something the task depends on, "
        "say so and ask instead of guessing.")


def _item_line(n: int, item: dict) -> str:
    quals = "; ".join(f"{q['type']}: {q['text']}" for q in item.get("qualifiers") or [] if q.get("text"))
    tail = f" ({quals})" if quals else ""
    scope = f" [scope: {item['scope_level']}]"
    if item.get("contested"):
        against = (item.get("contradicting") or {}).get("text") or "recorded counter-evidence"
        return f"{n}. CONTESTED, not established: {item['text']}{tail}. Contradicting evidence: {against}.{scope}"
    return f"{n}. {item['text']}{tail}{scope}"


def render_memory_section(frame_body: dict) -> str:
    """The memory section for one frame (deterministic; provider-independent)."""
    lines = [f"Relevant memory (frame {RENDER_VERSION}; coverage: {frame_body['coverage']}):"]
    items = frame_body.get("items") or []
    lines += [_item_line(n, it) for n, it in enumerate(items, 1)] if items else ["(none)"]
    if frame_body["coverage"] in ("weak", "none"):
        lines.append(_ASK.format(coverage=frame_body["coverage"]))
    return "\n".join(lines) + "\n"


def frame_id_of(frame_body: dict) -> str:
    """The frame's id: sha256 hex of its deterministic CBOR (as recall/assemble_frame.py computes it)."""
    return hashlib.sha256(encode_cbor(frame_body)).hexdigest()


def render_request(frame_body: dict, *, task: str, provider: str, model: str, params: ModelParams,
                   purpose: str, system: str) -> ModelRequest:
    """The provider-neutral request: the caller's system prompt, then the memory section, then the task."""
    content = render_memory_section(frame_body) + "\nTask:\n" + task
    return ModelRequest(provider=provider, model=model, messages=(Message("user", content),), params=params,
                        purpose=purpose, system=system, frame_id=frame_id_of(frame_body))
