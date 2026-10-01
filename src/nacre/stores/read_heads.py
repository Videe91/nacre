"""
Functionality: Read the interpretation heads of a stream as of a watermark, with their status and content.
Owns: resolving each object's head at or before commit_seq N from the projection, decrypting its content from the
  version event, and the recall-eligibility filter (no contested or superseded heads, no fallback to older versions).
Public entry: read_heads(), Head
Decisions: D-0017, D-0023
Assumptions: none
Notes: D-0017 / MNEXA ledger 38: a contested or superseded head is withheld from recall-eligible reads, and an OLDER
  active version is NOT shown instead. `fallback` records are recall-eligible and marked as such (status "fallback").
  as_of=N reads the head among versions committed at or before N, so history is reproducible (MNEXA ADR-0010).
  Content comes from the ledger (decrypted); a shredded head's content is None.
  D-0017 amendment 2: only the stream's ACTIVE projection generation is read.
  D-0023: a head whose content can no longer be decrypted (a contributor was erased) is lost: excluded unless
  include_unreadable=True (structure-only views such as audits).
"""
from dataclasses import dataclass
from uuid import UUID

from nacre.core.root_key_provider import RootKeyProvider
from nacre.scopes.open_scoped_session import ScopedSession
from nacre.stores.write_version import read_version_events

_HIDDEN = ("contested", "superseded")


@dataclass(frozen=True)
class Head:
    object_id: UUID
    version: int
    kind: str
    status: str
    support: str | None
    commit_seq: int
    content: dict | None


def read_heads(session: ScopedSession, key_provider: RootKeyProvider, stream_id: UUID, *, as_of: int | None = None,
               include_inactive: bool = False, include_unreadable: bool = False,
               kinds: tuple[str, ...] = ("belief", "fallback")) -> list[Head]:
    """Heads of `stream_id` at watermark `as_of` (default: now), in object_id order."""
    rows = session.conn.execute(
        "SELECT DISTINCT ON (object_id) object_id, version, kind, status, support, commit_seq, event_id "
        "FROM interp.versions WHERE stream_id = %s AND generation = interp.active_generation(stream_id) "
        "AND (%s::bigint IS NULL OR commit_seq <= %s::bigint) "
        "ORDER BY object_id, version DESC", (stream_id, as_of, as_of)).fetchall()
    bodies = {e.envelope.event_id: e.body["content"]["content"] for e in read_version_events(session, key_provider, stream_id)}
    out = []
    for object_id, version, kind, status, support, seq, event_id in rows:
        if kind not in kinds or (status in _HIDDEN and not include_inactive):
            continue
        if bodies.get(event_id) is None and not include_unreadable:
            continue                                  # D-0023: a lost (shredded) head is not recall-eligible
        out.append(Head(object_id, version, kind, status, support, seq, bodies.get(event_id)))
    return out
