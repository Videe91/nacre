"""
Functionality: Contest a belief when enough independent contradictions agree.
Owns: collecting contradiction proposals pinned to the CURRENT head version, grouping them (judged links by target
  head, other proposals by normalised text) with one per decision, the quorum (>= 2 distinct decisions), and the
  `contested` version.
Public entry: contest_belief(), CONTEST_QUORUM
Decisions: D-0017, D-0030
Assumptions: A-0027
Notes: MNEXA ledger 38 rules, with deviation 3: a no-op when the head is already contested OR superseded. The new
  version keeps the text and support, carries every edge forward, and adds `contradiction` edges to the agreeing
  proposals (each edge names the link's event id: the identity a later withdrawal will reference, D-0030 S2). The
  first group (commit order) to reach quorum wins. Single-source beliefs are contested by exactly the same rule
  (D-0017 amendment 1: no protection).
  D-0030 owner condition 1 (the two-vote quorum): judged links (`link: "judged"`, grounded by the sleep pass) form
  ONE group per target head version, whatever their text: >= 2 DISTINCT decisions (the resolved decisions the links
  record) contest it. Proposals without a `link` keep the old rule (grouped by identical normalised text). The two
  kinds never combine. Explicit links (`link: "explicit"`, no decision) do not count toward any quorum: the quorum
  counts decisions (D1 reading, reported to the owner as an open gap).
"""
from uuid import UUID

from nacre.core.root_key_provider import RootKeyProvider
from nacre.ledger.read_stream import read_stream
from nacre.scopes.open_scoped_session import ScopedSession
from nacre.stores.promote_if_supported import Promotion
from nacre.stores.write_version import Edge, VersionRecord, edges_from_body, read_version_events, write_version

CONTEST_QUORUM = 2


def contest_belief(session: ScopedSession, key_provider: RootKeyProvider, stream_id: UUID, belief_object_id: UUID,
                   *, cycle_id: UUID | None = None) -> Promotion | None:
    """Write a contested version if a quorum of agreeing contradictions targets the head; return the head after."""
    heads = [v for v in read_version_events(session, key_provider, stream_id)
             if v.body["content"]["object_id"] == str(belief_object_id) and v.body["content"]["kind"] == "belief"]
    if not heads:
        return None
    head = heads[-1].body["content"]
    if head["status"] != "active":
        return Promotion(belief_object_id, head["version"], head["status"], head["support"], False)
    groups: dict[tuple[str, str], dict[str, object]] = {}
    for e in read_stream(session, key_provider, stream_id):
        c = e.body.get("content") if isinstance(e.body, dict) else None
        if isinstance(c, dict) and c.get("op") == "contradiction_proposed" and c["target_object_id"] == str(belief_object_id) \
                and c["target_version"] == head["version"]:
            if c.get("decision_id") is None:
                continue                                    # explicit link: no decision to count
            group = ("judged", c["target_event_id"]) if c.get("link") == "judged" else ("text", c["key"])
            groups.setdefault(group, {}).setdefault(c["decision_id"], e)
    winner = next((g for g in groups.values() if len(g) >= CONTEST_QUORUM), None)
    if winner is None:
        return Promotion(belief_object_id, head["version"], head["status"], head["support"], False)
    content = dict(head["content"], contradiction_decisions=sorted(winner),
                   contradiction_text=next(iter(winner.values())).body["content"]["text"])
    edges = edges_from_body(head["edges"]) + [Edge("contradiction", target_event_id=e.envelope.event_id) for e in winner.values()]
    write_version(session, key_provider, stream_id, VersionRecord(
        belief_object_id, head["version"] + 1, "belief", "contested", head["support"], content, tuple(edges)),
        caused_by=next(iter(winner.values())).envelope.event_id, cycle_id=cycle_id, carried_from=heads[-1].envelope.event_id)
    return Promotion(belief_object_id, head["version"] + 1, "contested", head["support"], True)
