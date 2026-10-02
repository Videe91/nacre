"""Tests for recall/freeze_snapshot.py (R12, D-0025 §1): one consistent per-stream position vector read inside a
grant-confirmed snapshot session; concurrent appends to several streams never produce a torn cut (A-0037)."""
import threading
import uuid

import pytest

from recall_kit import belief
from nacre.core.event import ActorKind, EventType, PayloadType, Source
from nacre.ledger.append_event import AppendRequest, Authorship, append_event
from nacre.recall.freeze_snapshot import SnapshotRefused, freeze_snapshot


def _note(s, provider, stream, text="note"):
    return append_event(s, provider, AppendRequest(
        stream_id=stream, event_type=EventType.MESSAGE, payload_type=PayloadType.TEXT, actor_kind=ActorKind.AGENT,
        actor_id=uuid.UUID(int=5), source=Source.CHAT, authorship=Authorship.SCOPE_PRINCIPAL,
        idempotency_key=str(uuid.uuid4()), content=text)).envelope


def test_positions_generations_and_embedder_are_read_once(rw, snap, provider, streams):
    with rw() as s:
        belief(s, provider, streams["a"], "Pin the payments base image by digest 2026.", "Pin the payments base image")
    with snap() as s:
        snapshot = freeze_snapshot(s, [streams["a"]])
        p = snapshot.position(streams["a"])
        assert p.commit_seq == s.conn.execute("SELECT max(commit_seq) FROM ledger.events").fetchone()[0]
        assert (p.shred_epoch, p.projection_generation, p.index_generation) == (0, 1, 1) and p.embedder_id
    assert snapshot.canonical() == [[str(streams["a"]), p.commit_seq, 0, 1, 1, p.embedder_id]]


def test_it_refuses_a_non_snapshot_session_and_an_ungranted_stream(rw, snap, streams):
    with rw() as s, pytest.raises(SnapshotRefused, match="snapshot session"):
        freeze_snapshot(s, [streams["a"]])
    with snap() as s, pytest.raises(SnapshotRefused, match="no read grant"):
        freeze_snapshot(s, [streams["a"], streams["b"]])


def test_a_stream_with_nothing_indexed_has_no_index_generation(snap, grant, principal, streams):
    grant(principal, streams["b"], append=False)
    with snap() as s:
        p = freeze_snapshot(s, [streams["b"]]).position(streams["b"])
    assert (p.commit_seq, p.index_generation, p.embedder_id) == (0, None, None)


def test_concurrent_appends_to_several_streams_never_tear_the_cut(session, snap, grant, principal, provider, streams):
    # A-0037: commits that land while the snapshot is open are invisible to it, in EVERY stream, and the positions read
    # first and last agree with a re-read inside the same snapshot.
    a, b = streams["a"], streams["b"]
    grant(principal, a, append=False)
    grant(principal, b, append=False)
    writer = uuid.uuid4()
    stop = threading.Event()

    def keep_writing():
        while not stop.is_set():
            with session(writer, read=[a, b], write=[a, b]) as w:
                _note(w, provider, a)
                _note(w, provider, b)               # one transaction: both streams or neither
    t = threading.Thread(target=keep_writing)
    t.start()
    try:
        for _ in range(15):
            with snap() as s:
                first = freeze_snapshot(s, [a, b])
                again = freeze_snapshot(s, [a, b])
                assert first == again
                pa, pb = first.position(a).commit_seq, first.position(b).commit_seq
                writer_rows_a = s.conn.execute("SELECT count(*) FROM ledger.events WHERE stream_id = %s AND actor_id = %s",
                                               (a, uuid.UUID(int=5))).fetchone()[0]
                writer_rows_b = s.conn.execute("SELECT count(*) FROM ledger.events WHERE stream_id = %s AND actor_id = %s",
                                               (b, uuid.UUID(int=5))).fetchone()[0]
                assert writer_rows_a == writer_rows_b          # never one stream's half of a transaction
                assert pa >= writer_rows_a and pb >= writer_rows_b
    finally:
        stop.set()
        t.join(30)
