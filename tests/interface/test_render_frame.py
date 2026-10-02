"""Tests for interface/render_frame.py (R21, D-0025 §7 + amendment 1): deterministic, provider-independent memory
section; contested items never phrased as fact and placed as the frame orders them; coverage instruction."""
import hashlib
import types
import uuid

from nacre.core.encode_cbor import encode_cbor
from nacre.core.model_provider import ModelParams, canonical_request
from nacre.interface.render_frame import frame_id_of, render_memory_section, render_request
from nacre.recall.assemble_frame import assemble_frame


def _frame(coverage="strong", contested=True):
    items = [{"text": "Pin the payments base image by digest", "scope_level": "project", "contested": False,
              "qualifiers": [{"type": "condition", "text": "before release"}]},
             {"text": "Run the schema migration check", "scope_level": "task", "contested": False, "qualifiers": []}]
    if contested:
        items.append({"text": "Retry payments three times", "scope_level": "project", "contested": True,
                      "qualifiers": [], "contradicting": {"event_ids": ["e1", "e2"], "text": "never retry payments"}})
    return {"coverage": coverage, "items": items}


def test_the_memory_section_is_deterministic_and_keeps_frame_order():
    a, b = render_memory_section(_frame()), render_memory_section(_frame())
    assert a == b
    lines = a.splitlines()
    assert lines[1].startswith("1. Pin the payments base image by digest (condition: before release)")
    assert lines[2].startswith("2. Run the schema migration check") and lines[3].startswith("3. ")


def test_a_contested_item_is_never_phrased_as_fact_and_shows_its_evidence():
    line = render_memory_section(_frame()).splitlines()[3]
    assert line.startswith("3. CONTESTED, not established: Retry payments three times")
    assert "Contradicting evidence: never retry payments" in line
    assert "\n3. Retry payments three times" not in render_memory_section(_frame())


def test_weak_or_empty_coverage_asks_instead_of_guessing():
    assert "ask instead of guessing" in render_memory_section(_frame("weak"))
    assert "ask instead of guessing" in render_memory_section({"coverage": "none", "items": []})
    assert "ask instead of guessing" not in render_memory_section(_frame("strong"))
    assert "(none)" in render_memory_section({"coverage": "none", "items": []})


def test_the_request_is_identical_across_providers_apart_from_the_provider_fields():
    p = ModelParams(max_tokens=500)
    o = render_request(_frame(), task="Decide.", provider="openai", model="gpt-4o-mini-2024-07-18", params=p,
                       purpose="transfer", system="sys")
    a = render_request(_frame(), task="Decide.", provider="anthropic", model="claude-haiku-4-5-20251001", params=p,
                       purpose="transfer", system="sys")
    assert o.messages == a.messages and o.system == a.system


def test_the_request_names_its_frame_without_changing_the_prompt():
    """D-0022 amendment 1 / D-0028 §3: frame_id is metadata; the prompt bytes are what they were."""
    frame = _frame()
    r = render_request(frame, task="t", provider="openai", model="gpt-4o-mini-2024-07-18",
                       params=ModelParams(max_tokens=5), purpose="seat", system="s")
    assert r.frame_id == frame_id_of(frame) == hashlib.sha256(encode_cbor(frame)).hexdigest()
    assert r.messages[0].content == render_memory_section(frame) + "\nTask:\nt"
    assert "frame_id" not in canonical_request(r) and canonical_request(r)["v"] == 1        # D-0022 am. 2
    other = render_request(_frame(contested=False), task="t", provider="openai", model="gpt-4o-mini-2024-07-18",
                           params=ModelParams(max_tokens=5), purpose="seat", system="s")
    assert other.frame_id != r.frame_id


def test_frame_id_of_is_assemble_frames_id_rule():
    snapshot = types.SimpleNamespace(canonical=lambda: {"streams": [], "as_of": 3})
    f = assemble_frame(None, None, snapshot=snapshot, scopes=[("project", uuid.UUID(int=1))],
                       principal_id=uuid.UUID(int=2), query_text="q", addresses=[], relaxations=[], candidates=[],
                       ranked=[], active={"semantic": True}, texts={}, coverage="none")
    assert frame_id_of(f.body) == f.frame_id
