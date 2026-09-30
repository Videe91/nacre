"""
Functionality: Read a stream in commit order: from a sequence number, optionally as of a watermark, decrypted where
  the key still exists.
Owns: the AS_OF(N) read (MNEXA ADR-0010 rules 9, 13), the readability check, row → Envelope mapping, and
  per-event decryption into a body or Shredded.
Public entry: read_stream(), head(), ReadEvent
Decisions: D-0002, D-0003, D-0004, D-0005
Assumptions: A-0012
Notes: Only commit_seq orders; timestamps never order or filter a read (D-0002 time rules).
  AS_OF(N) returns exactly events 1..N of the stream (restricted to from_seq..N). N must not exceed the current
  head: a watermark beyond the head is not an exposed watermark, and later commits could still appear at or below
  it, breaking the stability guarantee. So it is refused, never clamped. (D1, per ADR-0010 rule 9.)
  A stream that is not readable in the session is refused explicitly (RLS would otherwise return nothing, which
  a caller could mistake for an empty stream). Shredded events are returned as Shredded bodies, not dropped.
"""
from dataclasses import dataclass, fields
from uuid import UUID

import psycopg

from nacre.core.event import (ActorKind, Envelope, EventType, Mode, PayloadType, Source, TimeBasis, TimePrecision,
                              Trust, TrustBasis)
from nacre.core.root_key_provider import RootKeyProvider
from nacre.keys.decrypt_payload import Shredded, decrypt_payload
from nacre.scopes.open_scoped_session import ScopedSession

_COLUMNS = [f.name for f in fields(Envelope)]
_ENUMS = {"event_type": EventType, "payload_type": PayloadType, "actor_kind": ActorKind, "source": Source,
          "trust": Trust, "occurred_at_basis": TimeBasis, "occurred_at_precision": TimePrecision, "mode": Mode,
          "trust_basis": TrustBasis}
_AAD = ("envelope_version", "event_id", "stream_id", "key_id", "event_type", "payload_type")


@dataclass(frozen=True)
class ReadEvent:
    envelope: Envelope
    body: dict | Shredded | None        # None when read with decrypt=False


class ReadError(LookupError):
    """The read cannot be answered truthfully (unreadable stream, or a watermark beyond the head)."""


def head(session: ScopedSession, stream_id: UUID) -> int:
    """The current watermark of the stream: its highest commit_seq (0 when empty)."""
    _require_readable(session, stream_id)
    row = session.conn.execute("SELECT max(commit_seq) FROM ledger.events WHERE stream_id = %s", (stream_id,)).fetchone()
    return row[0] or 0


def read_stream(session: ScopedSession, provider: RootKeyProvider, stream_id: UUID, *, from_seq: int = 1,
                as_of: int | None = None, limit: int | None = None, decrypt: bool = True) -> list[ReadEvent]:
    """Events from_seq..as_of (or ..head), in commit order."""
    _require_readable(session, stream_id)
    if type(from_seq) is not int or from_seq < 1:
        raise ReadError("from_seq must be an int >= 1")
    current = head(session, stream_id)
    upper = current if as_of is None else as_of
    if type(upper) is not int or upper < 0:
        raise ReadError("as_of must be an int >= 0")
    if upper > current:
        raise ReadError(f"as_of={upper} is beyond the head ({current}); only exposed watermarks are stable (ADR-0010 r9)")
    sql = (f"SELECT {', '.join(_COLUMNS)} FROM ledger.events WHERE stream_id = %s AND commit_seq BETWEEN %s AND %s "
           f"ORDER BY commit_seq" + (" LIMIT %s" if limit is not None else ""))
    params = (stream_id, from_seq, upper) + ((limit,) if limit is not None else ())
    out = []
    for row in session.conn.execute(sql, params):
        env = envelope_from_row(row)
        body = None
        if decrypt:
            body = decrypt_payload(session.conn, provider, {k: getattr(env, k) for k in _AAD}, env.body_ciphertext)
        out.append(ReadEvent(env, body))
    return out


def envelope_from_row(row) -> Envelope:
    values = {}
    for name, value in zip(_COLUMNS, row):
        if isinstance(value, memoryview):
            value = bytes(value)
        if name in _ENUMS and value is not None:
            value = _ENUMS[name](value)
        values[name] = value
    return Envelope(**values)


def _require_readable(session: ScopedSession, stream_id: UUID) -> None:
    if stream_id not in session.access.read_streams:
        raise ReadError(f"stream {stream_id} is not readable for principal {session.access.principal_id}")
