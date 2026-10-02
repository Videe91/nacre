"""
Functionality: Find, deterministically, the belief heads an episode's trusted correction may contradict: same stream,
  at least one shared identity address, at most K, in a fixed order.
Owns: reading the current belief heads of one stream (from the version events), the episode's identity addresses,
  the candidate rule, its order and K.
Public entry: find_candidates(), read_belief_heads(), episode_addresses(), BeliefHead, Candidate, IDENTITY_TYPES, K,
  CANDIDATE_STATUSES
Decisions: D-0030, D-0017, D-0018, D-0025
Assumptions: A-0047
Notes: D-0030 owner condition 2: the judge compares only beliefs that share an address, in the same scope (stream).
  - Heads: stores/read_heads.py (the stream's active projection generation, include_inactive), joined to their
    version events for the event id. A head whose content is unreadable (shredded) is skipped, never replaced by an
    older version. Every status is returned (grounding needs to see a head that became superseded); only active or
    contested heads are candidates, never superseded.
  - Addresses (D-0018 amendment 2, D-0025 §3): an episode's = the union of the `addresses` in its events' readable
    content (decision, action when there is one, outcome); a version's = its content `addresses`. Only the identity
    types code / file / entity count (D-0030 question 4, K and the types as proposed; the owner's acceptance did not
    change them). Matching is exact on the whole canonical string (capture stores them canonical).
  - No shared address -> not a candidate. No candidate -> the judge is never called.
  - Order (D1, fixed so the judge request is reproducible and replayable): most shared addresses first, then the most
    recent head (higher commit_seq), then object id. The first K = 5 are kept.
  - Read-only: nothing is written here, so an episode without candidates leaves the ledger exactly as before.
"""
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from uuid import UUID

from nacre.core.root_key_provider import RootKeyProvider
from nacre.ledger.read_stream import ReadEvent
from nacre.scopes.open_scoped_session import ScopedSession
from nacre.stores.read_heads import read_heads
from nacre.stores.write_version import read_version_events

IDENTITY_TYPES = frozenset({"code", "file", "entity"})
K = 5
CANDIDATE_STATUSES = frozenset({"active", "contested"})


@dataclass(frozen=True)
class BeliefHead:
    stream_id: UUID
    object_id: UUID
    version: int
    status: str
    event_id: UUID              # the head's version event
    commit_seq: int
    support_text: str
    addresses: frozenset[str]   # identity-type addresses only


@dataclass(frozen=True)
class Candidate:
    head: BeliefHead
    shared: tuple[str, ...]     # sorted shared identity addresses


def _identity(addresses: Iterable) -> frozenset[str]:
    return frozenset(a for a in addresses if isinstance(a, str) and a.partition(":")[0] in IDENTITY_TYPES)


def read_belief_heads(session: ScopedSession, key_provider: RootKeyProvider, stream_id: UUID) -> dict[UUID, BeliefHead]:
    """The current head of every readable belief of `stream_id`, by object id (any status)."""
    events = {(v.body["content"]["object_id"], v.body["content"]["version"]): v
              for v in read_version_events(session, key_provider, stream_id)}
    out = {}
    for h in read_heads(session, key_provider, stream_id, include_inactive=True, kinds=("belief",)):
        v = events.get((str(h.object_id), h.version))
        if v is None or not isinstance(h.content, dict) or not isinstance(h.content.get("support_text"), str):
            continue
        out[h.object_id] = BeliefHead(stream_id, h.object_id, h.version, h.status, v.envelope.event_id, h.commit_seq,
                                      h.content["support_text"], _identity(h.content.get("addresses") or ()))
    return out


def episode_addresses(index: Mapping[UUID, ReadEvent], event_ids: Iterable[UUID]) -> frozenset[str]:
    """The identity addresses of the episode's events (their readable content's `addresses`)."""
    found = set()
    for eid in event_ids:
        e = index.get(eid)
        c = e.body.get("content") if e is not None and isinstance(e.body, dict) else None
        if isinstance(c, dict):
            found |= _identity(c.get("addresses") or ())
    return frozenset(found)


def find_candidates(heads: Mapping[UUID, BeliefHead], addresses: frozenset[str], *, k: int = K) -> list[Candidate]:
    """The candidate belief heads for an episode with identity `addresses` (at most k, in the fixed order)."""
    out = [Candidate(h, tuple(sorted(h.addresses & addresses))) for h in heads.values()
           if h.status in CANDIDATE_STATUSES and h.addresses & addresses]
    out.sort(key=lambda c: (-len(c.shared), -c.head.commit_seq, str(c.head.object_id)))
    return out[:k]
