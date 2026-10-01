"""
Functionality: Decide whether one outcome section is authoritative, from the envelope only.
Owns: the D-0018 authority rule (D3, owner-approved), the closed set of section roles, and the reason a section is
  not authoritative.
Public entry: section_authority(), SECTION_ROLES, Authority
Decisions: D-0018, D-0012, D-0017
Assumptions: A-0026, A-0012
Notes: A section is authoritative only if ALL hold:
    role is `correction`, or `evaluation` on an outcome with success = false;
    the event is trust = trusted (D-0012 derives trust from source AND authorship, so a trusted event was not
      external-authored);
    the event's source is ci / review / git, or its actor is a person.
  Text, markers and model output can never make a section authoritative: this function never reads the text.
  D-0017 amendment 1: single-source promotion is allowed only for spans inside a section this function marks
  authoritative.
"""
from dataclasses import dataclass

from nacre.core.event import ActorKind, Envelope, EventType, Source, Trust

SECTION_ROLES = frozenset({"status", "evaluation", "correction", "diagnostic", "operator_note"})
_AUTHORITATIVE_SOURCES = frozenset({Source.CI, Source.REVIEW, Source.GIT})


@dataclass(frozen=True)
class Authority:
    authoritative: bool
    reason: str


def section_authority(envelope: Envelope, role: str, success: bool | None) -> Authority:
    """Authority of a section with `role` in the outcome `envelope` (whose body records `success`)."""
    if envelope.event_type != EventType.OUTCOME:
        return Authority(False, "not an outcome event")
    if role not in SECTION_ROLES:
        return Authority(False, f"unknown section role {role!r}")
    if not (role == "correction" or (role == "evaluation" and success is False)):
        return Authority(False, f"role {role!r} is evidence, not authority")
    if envelope.trust != Trust.TRUSTED:
        return Authority(False, "the event is untrusted")
    if envelope.source not in _AUTHORITATIVE_SOURCES and envelope.actor_kind != ActorKind.PERSON:
        return Authority(False, f"source {envelope.source} without a person actor")
    return Authority(True, "trusted correction")
