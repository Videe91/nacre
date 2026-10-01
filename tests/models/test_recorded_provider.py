"""Tests for models/recorded_provider.py (D-0022 recorded mode; SI-5, SI-6, SI-7)."""
import uuid

import psycopg
import pytest

from conftest import MODEL, FakeProvider, ok, req
from nacre.core.model_provider import CallPolicy
from nacre.ledger.read_stream import ReadError
from nacre.models.call_model import call_model
from nacre.models.recorded_provider import RecordedProvider, RecordingMiss


def _record(world, provider, responses, request=None):
    src = world["source"]()
    with world["open"](world["owner"]) as s:
        for r in responses:
            call_model(s, provider, FakeProvider([r]), request or req(), source_event_ids=[src], run_id=uuid.uuid4())
    return src


def test_replay_returns_recorded_responses_in_order_with_no_network_and_no_new_events(world, provider, no_network):
    world["allow"](MODEL)
    src = _record(world, provider, [ok("first", rid="r1"), ok("second", rid="r2")])
    with world["open"](world["owner"]) as s:
        rp = RecordedProvider(s, provider, [world["proj"]])
        before = s.conn.execute("SELECT count(*) FROM ledger.events").fetchone()[0]
        a = call_model(s, provider, rp, req(), source_event_ids=[src], run_id=uuid.uuid4())
        b = call_model(s, provider, rp, req(), source_event_ids=[src], run_id=uuid.uuid4())
        assert (a.response.text, b.response.text) == ("first", "second") and a.event_id is None
        assert s.conn.execute("SELECT count(*) FROM ledger.events").fetchone()[0] == before    # replay never records
        with pytest.raises(RecordingMiss):
            call_model(s, provider, rp, req(), source_event_ids=[src], run_id=uuid.uuid4())     # used up
        with pytest.raises(RecordingMiss):
            rp.complete(req(content="a different prompt"), timeout_s=1)


def test_failed_and_redacted_attempts_are_not_replayable(world, provider):
    from conftest import transient
    world["allow"](MODEL)
    token = "gh" + "p_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
    src = world["source"]()
    with world["open"](world["owner"]) as s:
        call_model(s, provider, FakeProvider([transient()]), req(), source_event_ids=[src], run_id=uuid.uuid4(),
                   policy=CallPolicy(max_attempts=1))
        call_model(s, provider, FakeProvider([ok(f"key {token}")]), req(), source_event_ids=[src], run_id=uuid.uuid4())
    with world["open"](world["owner"]) as s:
        assert RecordedProvider(s, provider, [world["proj"]]).recordings == 0


def test_si5_recordings_are_not_replayable_outside_their_scope(world, provider):
    world["allow"](MODEL)
    _record(world, provider, [ok()])
    with world["open"](uuid.uuid4()) as s, pytest.raises(ReadError):
        RecordedProvider(s, provider, [world["proj"]])


def test_si7_shredding_the_scope_makes_recordings_unreplayable(world, provider, migrated_db):
    world["allow"](MODEL)
    _record(world, provider, [ok()])
    with psycopg.connect(migrated_db["admin"]) as c:
        c.execute("DELETE FROM keys.stream_master_keys WHERE stream_id = %s", (world["proj"],))
    with world["open"](world["owner"]) as s:
        assert RecordedProvider(s, provider, [world["proj"]]).recordings == 0
