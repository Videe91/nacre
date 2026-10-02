"""Tests for models/call_model.py (D-0021 incl. amendment 1, D-0022; SI-1, SI-3)."""
import uuid
from decimal import Decimal

import pytest

from model_fakes import MODEL, FakeProvider, ok, req, transient
from nacre.core.event import ActorKind, EventType, Trust
from nacre.core.model_provider import CallPolicy, ProviderError, request_sha256
from nacre.ledger.read_stream import read_stream
from nacre.models.call_model import ModelCallRefused, call_model


def _calls(world, provider, stream=None):
    with world["open"](world["owner"]) as s:
        return [e for e in read_stream(s, provider, stream or world["proj"])
                if e.envelope.event_type == EventType.RESULT and e.envelope.actor_kind == ActorKind.MODEL]


def _call(world, provider, fake, request=None, sources=None, **kw):
    run = kw.pop("run_id", uuid.uuid4())
    with world["open"](world["owner"]) as s:
        return call_model(s, provider, fake, request or req(), source_event_ids=sources or [world["source"]()],
                          run_id=run, sleep=lambda _: None, **kw)


def test_a_call_is_recorded_with_pin_policy_settings_tokens_and_cost(world, provider):
    world["allow"](MODEL)
    run = uuid.uuid4()
    r = _call(world, provider, FakeProvider([ok()]), run_id=run, policy=CallPolicy(timeout_s=30.0, max_attempts=2, backoff_s=1.5))
    assert r.response.text == "the lesson" and r.attempts == 1
    assert r.cost_usd == Decimal("0.00045")                     # 1000 * 0.15/1M + 500 * 0.60/1M
    (e,) = _calls(world, provider)
    env, c = e.envelope, e.body["content"]
    assert env.event_id == r.event_id and env.trust == Trust.UNTRUSTED and env.cycle_id == run
    assert env.actor_model == MODEL and env.actor_model_version == MODEL
    assert c["status"] == "ok" and c["request_sha256"] == request_sha256(req()) == r.request_sha256
    assert c["call_policy"] == {"timeout_s": "30.0", "max_attempts": 2, "backoff_s": "1.5"}
    assert c["response"]["usage"] == {"input_tokens": 1000, "output_tokens": 500, "cached_input_tokens": None}
    assert c["cost_usd"] == "0.0004500000" and c["price_table"] == "2026-10-02" and c["redacted"] is False
    assert c["request"]["params"]["temperature"] is None                     # provider default, recorded as such


@pytest.mark.parametrize("model,match", [("gpt-4o-mini", "dated pin"), ("gpt-4o-2024-08-06", "no price entry")])
def test_undated_or_unpriced_models_are_refused_before_any_call(world, provider, model, match):
    world["allow"](MODEL)
    fake = FakeProvider([ok()])
    with pytest.raises(ModelCallRefused, match=match):
        _call(world, provider, fake, request=req(model=model))
    assert fake.calls == [] and _calls(world, provider) == []


def test_default_deny_without_a_policy_or_for_another_model(world, provider):
    fake = FakeProvider([ok()])
    with pytest.raises(ModelCallRefused, match="default deny"):
        _call(world, provider, fake)
    world["allow"]("gpt-4o-mini-2099-01-01")                     # a different pin only
    with pytest.raises(ModelCallRefused, match="default deny"):
        _call(world, provider, fake)
    assert fake.calls == []


def test_the_latest_policy_wins(world, provider):
    world["allow"](MODEL)
    world["allow"]()                                              # revoked: empty allow-list
    with pytest.raises(ModelCallRefused, match="default deny"):
        _call(world, provider, FakeProvider([ok()]))


def test_si1_sources_must_be_readable_and_from_one_scope(world, provider):
    world["allow"](MODEL)
    fake = FakeProvider([ok()])
    with world["open"](world["owner"]) as s, pytest.raises(ModelCallRefused, match="name the events"):
        call_model(s, provider, fake, req(), source_event_ids=[], run_id=uuid.uuid4())
    with pytest.raises(ModelCallRefused, match="not readable"):
        _call(world, provider, fake, sources=[uuid.uuid4()])
    a, b = world["source"](world["proj"]), world["source"](world["other"])
    with pytest.raises(ModelCallRefused, match="one prompt, one scope"):
        _call(world, provider, fake, sources=[a, b])
    stranger = uuid.uuid4()
    with world["open"](stranger) as s:                           # no grants: the source is invisible
        with pytest.raises(ModelCallRefused, match="not readable"):
            call_model(s, provider, fake, req(), source_event_ids=[a], run_id=uuid.uuid4())
    assert fake.calls == []


def test_every_attempt_is_recorded_and_transient_errors_are_retried(world, provider):
    world["allow"](MODEL)
    slept = []
    with world["open"](world["owner"]) as s:
        r = call_model(s, provider, FakeProvider([transient(), ok()]), req(), source_event_ids=[world["source"]()],
                       run_id=uuid.uuid4(), policy=CallPolicy(max_attempts=3, backoff_s=2.0), sleep=slept.append)
    assert r.attempts == 2 and slept == [2.0]
    a, b = _calls(world, provider)
    assert (a.body["content"]["status"], a.body["content"]["attempt"], a.body["content"]["error_class"]) == ("error", 1, "rate_limit")
    assert (b.body["content"]["status"], b.body["content"]["attempt"]) == ("ok", 2)


def test_a_permanent_error_is_recorded_and_raised_without_retry(world, provider):
    world["allow"](MODEL)
    fake = FakeProvider([ProviderError("bad request", retryable=False, error_class="invalid_request"), ok()])
    r = _call(world, provider, fake)                              # the session commits the failed attempt
    with pytest.raises(ProviderError, match="bad request"):
        r.raise_for_error()
    assert r.response is None and r.attempts == 1 and len(fake.calls) == 1 and [e.body["content"]["status"] for e in _calls(world, provider)] == ["error"]


def test_exhausted_retries_raise_after_recording_each_attempt(world, provider):
    world["allow"](MODEL)
    r = _call(world, provider, FakeProvider([transient(), transient()]), policy=CallPolicy(max_attempts=2))
    with pytest.raises(ProviderError, match="rate limited"):
        r.raise_for_error()
    assert [e.body["content"]["attempt"] for e in _calls(world, provider)] == [1, 2]


def test_a_secret_shaped_response_is_marked_redacted(world, provider):
    world["allow"](MODEL)
    token = "gh" + "p_" + "".join(a + b for a, b in zip("AbCdEfGhIjKlMnOpQr", "123456789012345678"))    # built at runtime, never a literal
    _call(world, provider, FakeProvider([ok(text=f"use {token}")]))
    (e,) = _calls(world, provider)
    assert e.body["content"]["redacted"] is True and token not in e.body["content"]["response"]["text"]


def test_failed_attempts_survive_because_the_error_is_returned_not_raised(world, provider):
    # Raising inside the scoped transaction would roll back the recordings of the failed attempts.
    world["allow"](MODEL)
    with world["open"](world["owner"]) as s:
        r = call_model(s, provider, FakeProvider([transient()]), req(), source_event_ids=[world["source"]()],
                       run_id=uuid.uuid4(), policy=CallPolicy(max_attempts=1), sleep=lambda _: None)
    assert r.error is not None and r.event_id is not None
    assert [e.envelope.event_id for e in _calls(world, provider)] == [r.event_id]
