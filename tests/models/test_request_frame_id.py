"""D-0022 amendment 1 (owner, 2026-10-02): an optional `frame_id` on model calls. Recorded in the call's `result` event
but NOT part of the request identity (amendment 2): the canonical form stays version 1 and every request hashes
exactly as before, with or without a frame (golden digest plus every recorded EXP-0003 fixture line); old records load and replay unchanged."""
import hashlib
import json
import uuid
from dataclasses import replace
from pathlib import Path

import pytest

from model_fakes import MODEL, FakeProvider, ok, req
from nacre.core.encode_cbor import encode_cbor
from nacre.core.event import ActorKind, EventType
from nacre.core.model_provider import Message, ModelParams, ModelRequest, canonical_request, request_sha256
from nacre.ledger.read_stream import read_stream
from nacre.models.call_model import ModelCallRefused, call_model
from nacre.models.load_model_call_fixtures import export_model_calls, load_model_calls
from nacre.models.recorded_provider import RecordedProvider, RecordingMiss

FRAME_A, FRAME_B = hashlib.sha256(b"frame a").hexdigest(), hashlib.sha256(b"frame b").hexdigest()
FIXTURES = Path(__file__).resolve().parents[1] / "regression" / "exp0003" / "fixtures"
GOLDEN = ModelRequest("openai", MODEL, (Message("user", "hello"),), ModelParams(max_tokens=400, temperature=0.0),
                      "eval.exp0004.transfer.N", system="sys")
GOLDEN_SHA256 = "92554194261e4ce89e554af0289def56bcc7f87b907f44777afa627bc04e4b96"   # computed before amendment 1


def _rebuild(c: dict) -> ModelRequest:
    p = c["params"]
    f = (lambda x: None if x is None else float(x))
    return ModelRequest(c["provider"], c["model"], tuple(Message(m["role"], m["content"]) for m in c["messages"]),
                        ModelParams(p["max_tokens"], f(p["temperature"]), f(p["top_p"]), p["seed"],
                                    p["response_format"]), c["purpose"], c["system"], c.get("frame_id"))


def _bodies(world, provider):
    with world["open"](world["owner"]) as s:
        return [e.body["content"] for e in read_stream(s, provider, world["proj"])
                if e.envelope.event_type == EventType.RESULT and e.envelope.actor_kind == ActorKind.MODEL]


# ---- canonical form: version 1 always; frame_id is never hashed (amendment 2) ----

def test_a_request_without_a_frame_hashes_exactly_as_before():
    assert request_sha256(GOLDEN) == GOLDEN_SHA256
    c = canonical_request(GOLDEN)
    assert c["v"] == 1 and "frame_id" not in c


def test_every_recorded_fixture_request_still_has_its_recorded_hash():
    lines = [json.loads(x) for p in sorted(FIXTURES.rglob("*.jsonl")) for x in p.read_text().splitlines()]
    assert len(lines) > 100
    for rec in lines:
        r = _rebuild(rec["request"])
        assert canonical_request(r) == rec["request"]
        assert hashlib.sha256(encode_cbor(rec["request"])).hexdigest() == request_sha256(r) == rec["request_sha256"]


def test_a_frame_never_changes_the_canonical_form_or_the_hash():
    framed = replace(GOLDEN, frame_id=FRAME_A)
    assert canonical_request(framed) == canonical_request(GOLDEN) and canonical_request(framed)["v"] == 1
    assert request_sha256(GOLDEN) == request_sha256(framed) == request_sha256(replace(GOLDEN, frame_id=FRAME_B))


@pytest.mark.parametrize("bad", ["", "ABC", FRAME_A.upper(), FRAME_A[:-1], FRAME_A + "0", 7])
def test_a_malformed_frame_id_is_refused_before_any_call(world, provider, bad):
    world["allow"](MODEL)
    with pytest.raises(ValueError):
        canonical_request(replace(req(), frame_id=bad))
    fake = FakeProvider([ok()])
    with world["open"](world["owner"]) as s, pytest.raises(ModelCallRefused, match="frame_id"):
        call_model(s, provider, fake, replace(req(), frame_id=bad), source_event_ids=[world["source"]()],
                   run_id=uuid.uuid4())
    assert fake.calls == []


# ---- recording and replay ----

def test_the_result_event_names_the_frame_and_a_frameless_call_records_nothing(world, provider):
    world["allow"](MODEL)
    src = world["source"]()
    with world["open"](world["owner"]) as s:
        framed = call_model(s, provider, FakeProvider([ok()]), replace(req(), frame_id=FRAME_A), source_event_ids=[src],
                            run_id=uuid.uuid4())
        plain = call_model(s, provider, FakeProvider([ok()]), req(), source_event_ids=[src], run_id=uuid.uuid4())
    a, b = _bodies(world, provider)
    assert a["frame_id"] == FRAME_A and "frame_id" not in a["request"] and a["request"]["v"] == 1
    assert a["request_sha256"] == framed.request_sha256 == request_sha256(req())
    assert "frame_id" not in b and "frame_id" not in b["request"] and b["request"]["v"] == 1
    assert b["request_sha256"] == plain.request_sha256 == request_sha256(req())


def test_replay_matches_on_what_is_sent_not_on_the_frame(world, provider, no_network):
    # A recorded replay rebuilds fresh databases, where frame ids differ while the prompt bytes do not (amendment 2).
    world["allow"](MODEL)
    src = world["source"]()
    with world["open"](world["owner"]) as s:
        call_model(s, provider, FakeProvider([ok("recorded")]), replace(req(), frame_id=FRAME_A),
                   source_event_ids=[src], run_id=uuid.uuid4())
    with world["open"](world["owner"]) as s:
        rp = RecordedProvider(s, provider, [world["proj"]])
        again = call_model(s, provider, rp, replace(req(), frame_id=FRAME_B), source_event_ids=[src],
                           run_id=uuid.uuid4())
        with pytest.raises(RecordingMiss):                 # one recording answers once
            rp.complete(req(), timeout_s=1)
    assert again.response.text == "recorded" and again.event_id is None


def test_fixtures_round_trip_the_frame_and_old_lines_load_and_replay_unchanged(world, provider, tmp_path, no_network):
    world["allow"](MODEL)
    src = world["source"]()
    with world["open"](world["owner"]) as s:
        call_model(s, provider, FakeProvider([ok("framed")]), replace(req(), frame_id=FRAME_A), source_event_ids=[src],
                   run_id=uuid.uuid4())
        export_model_calls(s, provider, world["proj"], tmp_path / "f.jsonl")
    old = sorted(FIXTURES.rglob("*.jsonl"))[0]
    old_line = json.loads(old.read_text().splitlines()[0])
    anchor = world["source"](world["other"])
    with world["open"](world["owner"]) as s:
        load_model_calls(s, provider, world["other"], tmp_path / "f.jsonl", sources=(anchor,))
        load_model_calls(s, provider, world["other"], old, sources=(anchor,))
    with world["open"](world["owner"]) as s:
        loaded = [e.body["content"] for e in read_stream(s, provider, world["other"])
                  if e.envelope.actor_kind == ActorKind.MODEL]
        rp = RecordedProvider(s, provider, [world["other"]])
        assert rp.complete(replace(req(), frame_id=FRAME_A), timeout_s=1).text == "framed"
        assert rp.complete(_rebuild(old_line["request"]), timeout_s=1).text == old_line["response"]["text"]
    assert loaded[0]["frame_id"] == FRAME_A and "frame_id" not in loaded[0]["request"]
    assert all("frame_id" not in b and b["request"] == json.loads(x)["request"]
               for b, x in zip(loaded[1:], old.read_text().splitlines()))
