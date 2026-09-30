"""Tests for core/event.py: the persisted envelope format (D-0002) must not drift silently."""
import dataclasses
from datetime import UTC, datetime
from uuid import UUID

import pytest

from nacre.core import event as ev


# The exact persisted values from D-0002. Changing any of these is a format change (new ADR).
EXPECTED_ENUMS = {
    ev.EventType: ["message", "action", "result", "prediction", "decision", "outcome",
                   "statement", "memory_event", "config_event", "correction", "deletion_marker"],
    ev.PayloadType: ["text", "image", "audio", "diff", "table", "structured", "trace"],
    ev.ActorKind: ["person", "agent", "model", "tool", "system"],
    ev.Source: ["chat", "git", "ci", "review", "web", "tool", "system"],
    ev.Trust: ["trusted", "untrusted"],
    ev.TimeBasis: ["observed", "asserted"],
    ev.TimePrecision: ["year", "month", "day", "hour", "minute", "second",
                       "millisecond", "microsecond"],
    ev.Mode: ["normal", "incident", "onboarding", "exploration"],
    ev.TrustBasis: ["asserted", "verified"],
}

EXPECTED_FIELDS = [
    "envelope_version", "event_id", "stream_id",
    "org_id", "project_id", "user_id", "agent_id", "task_id",
    "commit_seq", "occurred_at", "occurred_at_basis", "occurred_at_precision",
    "recorded_at", "committed_at", "event_type", "payload_type",
    "actor_kind", "actor_id", "actor_model", "actor_model_version", "actor_tool",
    "source", "trust", "caused_by", "cycle_id", "config_version", "mode",
    "trust_basis", "key_id", "idempotency_key", "request_mac", "attachment_ref", "attachment_sha256",
    "body_ciphertext", "prev_hash", "hash",
]


@pytest.mark.parametrize("enum_cls", list(EXPECTED_ENUMS))
def test_enum_values_match_d0002_exactly(enum_cls):
    assert [m.value for m in enum_cls] == EXPECTED_ENUMS[enum_cls]


def test_time_basis_has_no_inferred_member():
    # MNEXA ADR-0010 rule 3: inferred world time never enters the historical plane.
    assert "inferred" not in {m.value for m in ev.TimeBasis}


def test_envelope_fields_match_d0002_table():
    assert [f.name for f in dataclasses.fields(ev.Envelope)] == EXPECTED_FIELDS


def test_person_identity_is_not_a_plaintext_field():
    # D-0002 amendment 1: only a person's identity is encrypted, so no name/email column exists.
    names = {f.name for f in dataclasses.fields(ev.Envelope)}
    assert not names & {"actor", "actor_name", "person_name", "email", "display_name", "payload"}


def _sample(**overrides):
    now = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
    base = dict(
        envelope_version=ev.ENVELOPE_VERSION, event_id=ev.new_event_id(), stream_id=ev.new_event_id(),
        org_id=None, project_id=None, user_id=None, agent_id=None, task_id=None,
        commit_seq=1, occurred_at=None, occurred_at_basis=None, occurred_at_precision=None,
        recorded_at=now, committed_at=now, event_type=ev.EventType.MESSAGE,
        payload_type=ev.PayloadType.TEXT, actor_kind=ev.ActorKind.AGENT, actor_id=ev.new_event_id(),
        actor_model="claude-opus-5-5", actor_model_version=None, actor_tool=None,
        source=ev.Source.CHAT, trust=ev.Trust.UNTRUSTED, caused_by=None, cycle_id=None,
        config_version=None, mode=None, trust_basis=ev.TrustBasis.ASSERTED, key_id=ev.new_event_id(), idempotency_key="k1",
        request_mac=b"m", attachment_ref=None, attachment_sha256=None,
        body_ciphertext=b"c", prev_hash=bytes(32), hash=bytes(32),
    )
    base.update(overrides)
    return ev.Envelope(**base)


def test_envelope_is_immutable():
    e = _sample()
    with pytest.raises(dataclasses.FrozenInstanceError):
        e.commit_seq = 2


def test_envelope_requires_keyword_arguments():
    with pytest.raises(TypeError):
        ev.Envelope(1)  # positional construction would make field order a silent contract


def test_envelope_version_is_two():
    assert ev.ENVELOPE_VERSION == 2   # D-0002 amendment 4


def test_new_event_id_is_uuid_version_7():
    eid = ev.new_event_id()
    assert isinstance(eid, UUID)
    assert eid.version == 7


def test_new_event_ids_are_unique_and_time_sortable_in_generation_order():
    ids = [ev.new_event_id() for _ in range(10_000)]
    assert len(set(ids)) == len(ids)
    assert ids == sorted(ids)
