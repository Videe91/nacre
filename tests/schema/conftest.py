"""Helpers for schema tests: build and insert raw ledger rows (no crypto; the DB only checks shape)."""
import uuid
from datetime import UTC, datetime

import pytest

ZERO_HASH = bytes(32)
COLUMNS = [
    "envelope_version", "event_id", "stream_id", "commit_seq", "recorded_at", "committed_at",
    "event_type", "payload_type", "actor_kind", "actor_id", "source", "trust", "key_id",
    "idempotency_key", "request_mac", "body_ciphertext", "prev_hash", "hash",
]


def event_row(stream_id, seq, prev_hash, **overrides):
    now = datetime.now(UTC)
    row = {
        "envelope_version": 1, "event_id": uuid.uuid7(), "stream_id": stream_id, "commit_seq": seq,
        "recorded_at": now, "committed_at": now, "event_type": "message", "payload_type": "text",
        "actor_kind": "agent", "actor_id": uuid.uuid4(), "source": "chat", "trust": "untrusted",
        "key_id": uuid.uuid4(), "idempotency_key": f"k-{uuid.uuid4().hex}", "request_mac": bytes(32),
        "body_ciphertext": b"ciphertext", "prev_hash": prev_hash, "hash": uuid.uuid4().bytes * 2,
    }
    row.update(overrides)
    return row


def insert_event(conn, row):
    cols = list(row)
    conn.execute(
        f"INSERT INTO ledger.events ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})",
        [row[c] for c in cols])
    return row


def insert_chain(conn, stream_id, n):
    """Insert a valid n-event chain into `stream_id`; returns the rows."""
    rows, prev = [], ZERO_HASH
    for seq in range(1, n + 1):
        rows.append(insert_event(conn, event_row(stream_id, seq, prev)))
        prev = rows[-1]["hash"]
    return rows


def scope_to(conn, read=(), write=(), principal=None):
    """Transaction-local scope settings, as open_scoped_session will set them."""
    conn.execute("SELECT set_config('nacre.read_streams', %s, true)", ("{" + ",".join(map(str, read)) + "}",))
    conn.execute("SELECT set_config('nacre.write_streams', %s, true)", ("{" + ",".join(map(str, write)) + "}",))
    if principal is not None:
        conn.execute("SELECT set_config('nacre.principal', %s, true)", (str(principal),))


@pytest.fixture
def helpers():
    class H:
        pass
    h = H()
    h.event_row, h.insert_event, h.insert_chain, h.scope_to = event_row, insert_event, insert_chain, scope_to
    h.ZERO_HASH = ZERO_HASH
    return h
