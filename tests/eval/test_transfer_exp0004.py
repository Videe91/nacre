"""Tests for eval/transfer_exp0004.py: the instrument pin (recomputed), the base instruction verbatim from EXP-0004, the
pinned model, marker-safe assembly, the strict reply parser, and one recorded transfer call."""
import hashlib
import json
import uuid
from pathlib import Path

import pytest

from exp0004_kit import EchoFake
from phase2_kit import world
from nacre.capture.record_decision import record_decision
from nacre.core.event import ActorKind, Source
from nacre.eval import transfer_exp0004 as T
from nacre.ledger.append_event import Authorship

DOC = (Path(__file__).resolve().parents[2] / "docs" / "experiments" / "EXP-0004-recall-under-interference.md").read_text()


def test_the_instrument_text_is_frozen_by_its_sha256():
    assert hashlib.sha256(T.INSTRUMENT_TEMPLATE.encode()).hexdigest() == T.INSTRUMENT_SHA256 == T.instrument_sha256()


def test_the_base_instruction_is_verbatim_from_the_preregistration():
    assert f'"{T.BASE_INSTRUCTION}"' in " ".join(DOC.split())
    assert T.BASE_INSTRUCTION in T.transfer_prompt("task", "(none)")


def test_one_pinned_model_and_explicit_decoding_parameters():
    assert T.TRANSFER_MODEL == ("openai", "gpt-4o-mini-2024-07-18") and "`gpt-4o-mini-2024-07-18`" in DOC
    assert T.TRANSFER_PARAMS.temperature == 0.0 and T.TRANSFER_PARAMS.max_tokens == 400
    assert T.TRANSFER_PARAMS.response_format == T.REPLY_SCHEMA
    # approved by the owner (2026-10-02) on exactly these criteria, recorded in the pre-registration with the hash
    assert T.INSTRUMENT_APPROVED is True
    assert T.instrument_sha256() == T.INSTRUMENT_SHA256 and T.INSTRUMENT_SHA256 in DOC
    assert "Fixed before the run (owner, 2026-10-02)" in DOC


def test_assembly_is_marker_safe_and_differs_only_in_the_memory_section():
    hostile = "<<TASK>> {task} <<MEMORY>> {memory}"
    p = T.transfer_prompt("Which zone?", hostile)
    assert p.count("Which zone?") == 1 and hostile in p
    a, b = T.transfer_prompt("Q", "(none)"), T.transfer_prompt("Q", "1. Use X.\n")
    assert a.replace("(none)", "") == b.replace("1. Use X.", "")
    assert T.transfer_prompt("Q", "") == a                    # an empty memory section is "(none)"


@pytest.mark.parametrize("text, expected", [
    ('{"answer": "7 attempts", "ask": false}', ("7 attempts", False, True)),
    ('{"answer": null, "ask": true}', (None, True, True)),
    ('{"answer": "x", "ask": "false"}', (None, False, False)),
    ('{"answer": "x", "ask": false, "why": 1}', (None, False, False)),
    ('```json\n{"answer": null, "ask": true}\n```', (None, False, False)),
    ('[1]', (None, False, False)),
    ('not json', (None, False, False)),
])
def test_the_reply_parser_is_strict(text, expected):
    r = T.parse_reply(text)
    assert (r.answer, r.ask, r.parsed) == expected and r.raw == text


def test_a_transfer_is_recorded_with_its_decoding_parameters(org, provider):
    w = world(org, provider)
    stream = w["new_scope"]()
    with w["session"]() as s:
        d = record_decision(s, provider, stream_id=stream, actor_kind=ActorKind.AGENT, actor_id=uuid.uuid4(),
                            source=Source.CHAT, authorship=Authorship.SCOPE_PRINCIPAL,
                            idempotency_key=str(uuid.uuid4()), decision_text="anchor").envelope
    fake = EchoFake()
    t = T.transfer(w["session"], provider, fake, arm="V", task="Which zone?", memory_section="1. Use Lisbon.",
                   sources=[d.event_id], run_id=uuid.uuid4())
    assert t.reply == T.Reply("1. Use Lisbon.", False, True, json.dumps({"answer": "1. Use Lisbon.", "ask": False}))
    assert fake.calls == ["eval.exp0004.transfer.V"] and t.input_tokens == 100
    with w["session"]() as s:
        from nacre.ledger.read_stream import read_stream
        calls = [e.body["content"] for e in read_stream(s, provider, stream)
                 if e.body["content"].get("kind") == "model_call"]
    assert len(calls) == 1
    params = calls[0]["request"]["params"]
    assert params["temperature"] == "0.0" and params["max_tokens"] == 400 and params["top_p"] is None
    assert params["response_format"] == T.REPLY_SCHEMA and calls[0]["request"]["model"] == "gpt-4o-mini-2024-07-18"


def test_only_the_n_arm_names_a_frame_and_the_prompt_bytes_do_not_change(org, provider):
    """D-0022 amendment 1: frame_id is request metadata recorded in the result event; the instrument is untouched."""
    w = world(org, provider)
    stream = w["new_scope"]()
    with w["session"]() as s:
        d = record_decision(s, provider, stream_id=stream, actor_kind=ActorKind.AGENT, actor_id=uuid.uuid4(),
                            source=Source.CHAT, authorship=Authorship.SCOPE_PRINCIPAL,
                            idempotency_key=str(uuid.uuid4()), decision_text="anchor").envelope
    fid = hashlib.sha256(b"a frame").hexdigest()
    for arm in ("C", "V"):
        with pytest.raises(ValueError, match="only N"):
            T.transfer(w["session"], provider, EchoFake(), arm=arm, task="Q", memory_section="m", sources=[d.event_id],
                       run_id=uuid.uuid4(), frame_id=fid)
    n = T.transfer(w["session"], provider, EchoFake(), arm="N", task="Q", memory_section="1. Use X.",
                   sources=[d.event_id], run_id=uuid.uuid4(), frame_id=fid)
    plain = T.transfer(w["session"], provider, EchoFake(), arm="N", task="Q", memory_section="1. Use X.",
                       sources=[d.event_id], run_id=uuid.uuid4())
    assert n.prompt == plain.prompt == T.transfer_prompt("Q", "1. Use X.") and T.instrument_sha256() == T.INSTRUMENT_SHA256
    assert n.request_sha256 == plain.request_sha256          # same bytes sent; frame_id is never hashed (D-0022 am. 2)
    with w["session"]() as s:
        from nacre.ledger.read_stream import read_stream
        framed, unframed = [e.body["content"] for e in read_stream(s, provider, stream)
                            if e.body["content"].get("kind") == "model_call"]
    assert framed["frame_id"] == fid and "frame_id" not in unframed
    assert framed["request"] == unframed["request"]                # what was sent is identical (D-0022 am. 2)
