"""
Functionality: Promote a lesson proposal into a belief (or a fallback record) when its evidence is sufficient, and
  version it when new independent support arrives.
Owns: collecting agreeing proposals (same normalised key and kind) with real decision -> outcome ancestry, one per
  decision; the quorum rule; the single-source rule and its safeguards (D-0017 amendment 1); upgrades; carrying
  status, contradiction and supersession edges forward; MNEXA deviations 1-2.
Public entry: promote_if_supported(), Promotion, belief_object_id, SINGLE_SOURCE, QUORUM
Decisions: D-0017, D-0018, D-0020, D-0025
Assumptions: A-0027
Notes: Rules (D-0017 + amendment 1):
    no head:   >= 2 distinct decisions -> v1 support=quorum;  else 1 decision grounded in a TRUSTED CORRECTION span
               -> v1 support=single_source (lower confidence/strength); else nothing (outcome-inferred lessons need 2).
    head active: new independent decisions, or a single_source belief reaching 2 decisions -> new version
               (upgrade to quorum). Otherwise the head is returned unchanged (deviation 2: a retry returns the HEAD).
    head contested/superseded: new support is recorded as a new version with the SAME status. Promotion never
               re-activates (deviation 1); only supersession/contest change status.
  Kind: structured proposals form `belief`s; unresolved (support-only) proposals form `fallback` records under the
  same evidence rule (status `fallback`, no support level, D-0017 schema).
  Identity: object_id = uuid5 over (stream, kind, sha256(key)), the D-0017 "derived from the normalised text within
  the scope" identity. Starting values (placeholders, Phase 4 tunes them): single_source confidence 50 / strength 50,
  quorum 80 / 100 (per cent, integers: no floats in the CBOR subset).
  Ancestry (D-0020 amendment 1): a proposal counts only if its outcome resolves to its decision, directly or through
  its action's recorded link (sleep/build_evidence_bundle.resolve_outcome_decision). Quorum counts distinct
  RESOLVED decisions (D-0017), so two actions of one decision are one decision.
  Addresses (D-0025 §3): the version's `addresses` = write_version.source_addresses() over the capture events of
  every supporting proposal (its decision and its outcome, the proposal's own D-0023 sources). Recomputed for every
  version from the current supporting set, never copied from the head. Absent when no source has any.
"""
import hashlib
import uuid
from dataclasses import dataclass
from uuid import UUID

from nacre.core.root_key_provider import RootKeyProvider
from nacre.ledger.read_stream import read_stream
from nacre.scopes.open_scoped_session import ScopedSession
from nacre.sleep.build_evidence_bundle import BundleError, resolve_outcome_decision
from nacre.stores.write_version import (Edge, VersionRecord, edges_from_body, read_version_events, source_addresses,
                                        write_version)

SINGLE_SOURCE = {"confidence_pct": 50, "strength_pct": 50}
QUORUM = {"confidence_pct": 80, "strength_pct": 100}
_NS = uuid.UUID("0b5e7c1a-2d3f-4e6a-9b8c-1d2e3f4a5b6c")
_CARRIED = ("contradiction", "superseded_by")


@dataclass(frozen=True)
class Promotion:
    object_id: UUID
    version: int
    status: str
    support: str | None
    created: bool


def belief_object_id(stream_id: UUID, kind: str, key: str) -> UUID:
    return uuid.uuid5(_NS, f"{stream_id}|{kind}|{hashlib.sha256(key.encode()).hexdigest()}")


def _proposals(events, key, kind):
    by_id = {e.envelope.event_id: e for e in events}
    out = {}
    for e in events:
        c = e.body.get("content") if isinstance(e.body, dict) else None
        if not (isinstance(c, dict) and c.get("op") == "lesson_proposed" and c["key"] == key
                and ("belief" if c["structure_status"] == "structured" else "fallback") == kind):
            continue
        o = by_id.get(UUID(c["outcome_id"]))
        try:
            if o is None or resolve_outcome_decision(o, by_id).decision_id != UUID(c["decision_id"]):
                continue
        except BundleError:
            continue
        out.setdefault(c["decision_id"], e)            # earliest proposal per decision (commit order)
    return out


def promote_if_supported(session: ScopedSession, key_provider: RootKeyProvider, stream_id: UUID, proposal_id: UUID,
                         *, cycle_id: UUID | None = None) -> Promotion | None:
    """Promote or version the belief/fallback that `proposal_id` supports; None when the evidence is not enough."""
    events = read_stream(session, key_provider, stream_id)
    (p,) = [e for e in events if e.envelope.event_id == proposal_id] or [None]
    pc = p.body.get("content") if p is not None and isinstance(p.body, dict) else None
    if not (isinstance(pc, dict) and pc.get("op") == "lesson_proposed"):
        raise ValueError("proposal_id is not a readable lesson proposal of this stream")
    kind = "belief" if pc["structure_status"] == "structured" else "fallback"
    canon = _proposals(events, pc["key"], kind)
    if pc["decision_id"] not in canon:
        return None                                     # the proposal itself has no real ancestry
    n = len(canon)
    trusted = any(e.body["content"]["grounded_in_trusted_correction"] for e in canon.values())
    object_id = belief_object_id(stream_id, kind, pc["key"])
    heads = [v for v in read_version_events(session, key_provider, stream_id) if v.body["content"]["object_id"] == str(object_id)]
    head = heads[-1].body["content"] if heads else None
    decisions = sorted(canon)
    if head is None:
        if n < 2 and not trusted:
            return None
        status, version = ("active" if kind == "belief" else "fallback"), 1
    else:
        new = set(decisions) - set(head["content"]["support_decisions"])
        if not new:            # an upgrade (single_source -> quorum) always comes with a new supporting decision
            return Promotion(object_id, head["version"], head["status"], head["support"], False)
        status, version = head["status"], head["version"] + 1
    support = None if kind == "fallback" else ("quorum" if n >= 2 else "single_source")
    first = canon[decisions[0]].body["content"]
    by_id = {e.envelope.event_id: e for e in events}
    sources = [by_id[UUID(x)] for dec in decisions
               for x in (dec, canon[dec].body["content"]["outcome_id"])]
    content = {"key": pc["key"], "nucleus": first["nucleus"], "support_text": first["support_text"],
               "qualifiers": first["qualifiers"], "origin": "stated" if trusted else "inferred",
               "support_decisions": decisions, **source_addresses(sources),
               **(QUORUM if support == "quorum" else SINGLE_SOURCE if support else {}),
               **({k: head["content"][k] for k in ("contradiction_decisions", "superseded_by") if head and k in head["content"]})}
    edges = []
    for dec in decisions:
        c = canon[dec].body["content"]
        edges.append(Edge("derived_from", target_event_id=canon[dec].envelope.event_id))
        edges.append(Edge("support", target_event_id=UUID(c["outcome_id"]), span=(c["span_start"], c["span_end"]),
                          span_text=c["support_text"]))
    if head:
        edges += [e for e in edges_from_body(head["edges"]) if e.role in _CARRIED]
    write_version(session, key_provider, stream_id, VersionRecord(object_id, version, kind, status, support, content,
                                                                  tuple(edges)), caused_by=proposal_id, cycle_id=cycle_id,
                  carried_from=heads[-1].envelope.event_id if heads else None)
    return Promotion(object_id, version, status, support, True)
