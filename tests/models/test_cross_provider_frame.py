"""Cross-provider frame test, offline part (D-0028 §3; Phase 3 gate item 13). One frame body is rendered for an
OpenAI request and an Anthropic request: the memory-section bytes are identical and only the provider envelope
differs. Both calls go through call_model with the real adapters over fake SDK clients (no network, no key), and an
offline replay through RecordedProvider reproduces both with zero network calls. The live smoke run is the owner's.
NOT covered here: "both result events name the same frame_id in their request". ModelRequest (D-0021) has no field
that carries a frame_id and render_request does not put it in the request; how a call names its frame is an open D2
question, so this file does not invent a carrier."""
import types
import uuid

from anthropic.types import Message as SdkMessage

from nacre.core.model_provider import ModelParams
from nacre.interface.render_frame import render_memory_section, render_request
from nacre.models.anthropic_messages_provider import AnthropicMessagesProvider
from nacre.models.call_model import call_model
from nacre.models.openai_responses_provider import OpenAIResponsesProvider
from nacre.models.recorded_provider import RecordedProvider
from nacre.models.set_model_policy import set_model_policy

GPT, CLAUDE = "gpt-4o-mini-2024-07-18", "claude-haiku-4-5-20251001"
SYSTEM = "Answer from the memory below. Be brief."
TASK = "Should the CI job retry on a transient network failure?"
FRAME = {"v": 1, "coverage": "partial", "principal_id": str(uuid.UUID(int=7)), "items": [
    {"version_event_id": str(uuid.UUID(int=1)), "kind": "lesson", "status": "active", "scope_level": "project",
     "text": "CI says: retry with backoff", "qualifiers": [{"type": "condition", "text": "transient network errors"}],
     "contested": False},
    {"version_event_id": str(uuid.UUID(int=2)), "kind": "lesson", "status": "contested", "scope_level": "org",
     "text": "never retry CI jobs", "qualifiers": [], "contested": True,
     "contradicting": {"text": "the flaky-network postmortem"}}]}


class _Create:
    def __init__(self, result):
        self.result, self.calls = result, []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


def _openai_client(text):
    r = types.SimpleNamespace(output_text=text, status="completed", incomplete_details=None, id="resp_o", model=GPT,
                              usage=types.SimpleNamespace(input_tokens=120, output_tokens=8,
                                                          input_tokens_details=types.SimpleNamespace(cached_tokens=0)))
    create = _Create(r)
    return types.SimpleNamespace(responses=create), create


def _anthropic_client(text):
    r = SdkMessage.model_validate({
        "id": "msg_a", "type": "message", "role": "assistant", "model": CLAUDE, "stop_reason": "end_turn",
        "stop_sequence": None, "stop_details": None, "content": [{"type": "text", "text": text}],
        "usage": {"input_tokens": 130, "output_tokens": 9, "cache_read_input_tokens": 0,
                  "cache_creation_input_tokens": 0}})
    create = _Create(r)
    return types.SimpleNamespace(messages=create), create


def _requests():
    params = ModelParams(max_tokens=128, temperature=0.0)
    return tuple(render_request(FRAME, task=TASK, provider=p, model=m, params=params, purpose="transfer.reason",
                                system=SYSTEM) for p, m in (("openai", GPT), ("anthropic", CLAUDE)))


def test_the_memory_section_bytes_are_identical_and_only_the_envelope_differs():
    section = render_memory_section(FRAME).encode()
    o_req, a_req = _requests()
    assert o_req.messages == a_req.messages and o_req.system == a_req.system == SYSTEM
    assert o_req.messages[0].content.encode().startswith(section)
    oc, o_create = _openai_client("yes, with backoff")
    ac, a_create = _anthropic_client("yes, with backoff")
    OpenAIResponsesProvider(oc).complete(o_req, timeout_s=5)
    AnthropicMessagesProvider(ac).complete(a_req, timeout_s=5)
    (o_sent,), (a_sent,) = o_create.calls, a_create.calls
    o_user = o_sent["input"][0]["content"].encode()
    a_user = a_sent["messages"][0]["content"].encode()
    assert o_user == a_user and o_user[:len(section)] == section          # identical bytes reach both providers
    assert (o_sent["instructions"], a_sent["system"]) == (SYSTEM, SYSTEM)
    assert set(o_sent) - {"timeout"} == {"model", "max_output_tokens", "input", "instructions", "temperature"}
    assert set(a_sent) - {"timeout"} == {"model", "max_tokens", "messages", "system", "extra_body"}
    assert "CONTESTED, not established: never retry CI jobs" in section.decode()


def test_both_calls_are_recorded_and_an_offline_replay_reproduces_both(world, provider, no_network):
    with world["open"](world["owner"]) as s:
        set_model_policy(s, provider, org_id=world["org"], allowed=[("openai", GPT), ("anthropic", CLAUDE)],
                         idempotency_key=str(uuid.uuid4()))
    source = world["source"](text=FRAME["items"][0]["text"])
    o_req, a_req = _requests()
    oc, o_create = _openai_client("openai: yes, with backoff")
    ac, a_create = _anthropic_client("anthropic: yes, with backoff")
    run = uuid.uuid4()
    with world["open"](world["owner"]) as s:
        live = [call_model(s, provider, OpenAIResponsesProvider(oc), o_req, source_event_ids=[source], run_id=run),
                call_model(s, provider, AnthropicMessagesProvider(ac), a_req, source_event_ids=[source], run_id=run)]
    assert [c.response.text for c in live] == ["openai: yes, with backoff", "anthropic: yes, with backoff"]
    assert len(o_create.calls) == len(a_create.calls) == 1

    with world["open"](world["owner"]) as s:                              # offline: sockets refuse (no_network)
        replay = RecordedProvider(s, provider, [world["proj"]])
        assert replay.recordings == 2
        again = [call_model(s, provider, replay, r, source_event_ids=[source], run_id=uuid.uuid4())
                 for r in (o_req, a_req)]
    for was, now in zip(live, again):
        assert now.response.text == was.response.text and now.request_sha256 == was.request_sha256
        assert now.response.model_reported == was.response.model_reported and now.event_id is None
    assert len(o_create.calls) == len(a_create.calls) == 1                # replay never reached an SDK
