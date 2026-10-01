"""Tests for capture/section_authority.py: the D-0018 authority rule (D3), every condition."""
import dataclasses
import uuid
from datetime import UTC, datetime

import pytest

from nacre.capture.section_authority import section_authority
from nacre.core.event import ActorKind, Envelope, EventType, Source, Trust


def env(**over):
    base = {f.name: None for f in dataclasses.fields(Envelope)}
    base.update(event_type=EventType.OUTCOME, trust=Trust.TRUSTED, source=Source.REVIEW, actor_kind=ActorKind.SYSTEM,
                event_id=uuid.uuid4(), recorded_at=datetime.now(UTC))
    base.update(over)
    return Envelope(**base)


@pytest.mark.parametrize("source", [Source.CI, Source.REVIEW, Source.GIT])
def test_a_trusted_correction_from_an_integration_source_is_authoritative(source):
    assert section_authority(env(source=source), "correction", False).authoritative


def test_a_trusted_person_correction_from_chat_is_authoritative():
    assert section_authority(env(source=Source.CHAT, actor_kind=ActorKind.PERSON), "correction", None).authoritative


def test_a_failing_evaluation_is_authoritative_but_a_passing_or_unknown_one_is_not():
    assert section_authority(env(), "evaluation", False).authoritative
    assert not section_authority(env(), "evaluation", True).authoritative
    assert not section_authority(env(), "evaluation", None).authoritative


@pytest.mark.parametrize("role", ["status", "diagnostic", "operator_note"])
def test_evidence_roles_are_never_authoritative(role):
    a = section_authority(env(), role, False)
    assert not a.authoritative and "evidence" in a.reason


@pytest.mark.parametrize("over,why", [
    ({"trust": Trust.UNTRUSTED}, "untrusted"),
    ({"source": Source.TOOL, "actor_kind": ActorKind.TOOL}, "without a person"),
    ({"source": Source.WEB, "actor_kind": ActorKind.AGENT}, "without a person"),
    ({"source": Source.SYSTEM, "actor_kind": ActorKind.MODEL}, "without a person"),
    ({"event_type": EventType.STATEMENT}, "not an outcome"),
])
def test_untrusted_or_wrong_source_or_wrong_event_is_never_authoritative(over, why):
    a = section_authority(env(**over), "correction", False)
    assert not a.authoritative and why in a.reason


def test_unknown_roles_are_refused():
    assert not section_authority(env(), "authoritative_correction", False).authoritative     # MNEXA's marker name
