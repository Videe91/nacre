"""
Functionality: Replay one cognitive cycle of a stream: its events in commit order, as of a watermark.
Owns: selecting a cycle's events and checking that every causal link inside it points backwards in commit order.
Public entry: replay_cycle()
Decisions: D-0002, D-0003
Assumptions: none
Notes: Scoped to ONE stream (D1). Streams have separate gapless sequences, so there is no single order across
  streams; replaying a cycle that spans several streams needs a per-stream watermark vector (D-0002 time notes),
  which belongs to Phase 3 recall. Reading goes through read_stream, so the readability check, the AS_OF(N)
  stability rule, decryption and Shredded bodies behave exactly as there. A caused_by that points forward, or
  outside the stream, would mean a corrupted ledger; it is reported as an error, never reordered.
"""
from uuid import UUID

from nacre.core.root_key_provider import RootKeyProvider
from nacre.ledger.read_stream import ReadError, ReadEvent, read_stream
from nacre.scopes.open_scoped_session import ScopedSession


def replay_cycle(session: ScopedSession, provider: RootKeyProvider, stream_id: UUID, cycle_id: UUID, *,
                 as_of: int | None = None, decrypt: bool = True) -> list[ReadEvent]:
    """The cycle's events, in commit order, with causal links verified."""
    events = [e for e in read_stream(session, provider, stream_id, as_of=as_of, decrypt=decrypt)
              if e.envelope.cycle_id == cycle_id]
    seq_of = {e.envelope.event_id: e.envelope.commit_seq for e in read_stream(session, provider, stream_id,
                                                                              as_of=as_of, decrypt=False)}
    for e in events:
        parent = e.envelope.caused_by
        if parent is not None and not (parent in seq_of and seq_of[parent] < e.envelope.commit_seq):
            raise ReadError(f"event {e.envelope.event_id} has a caused_by that is not an earlier event of the stream")
    return events
