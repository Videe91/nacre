"""Tests for ledger/collect_orphan_blobs.py (D-0015 with owner amendments, A-0022)."""
import io
import os
import threading
import time
import uuid
from datetime import timedelta

import psycopg
import pytest
from PIL import Image

from nacre.core.event import ActorKind, EventType, PayloadType, Source
from nacre.ledger.append_event import AppendRequest, append_event
from nacre.ledger.collect_orphan_blobs import CollectionError, collect_orphan_blobs
from nacre.ledger.local_disk_blob_store import LocalDiskBlobStore
from nacre.ledger.read_attachment import read_attachment


def _png():
    """A real, clean PNG: binaries must pass the D-0027 scan, so a fake PNG header would now be unscannable."""
    out = io.BytesIO()
    Image.new("RGB", (64, 64), (200, 220, 240)).save(out, "PNG")
    return out.getvalue()


PNG = _png()
DAY = 24 * 3600


def req(stream, data=PNG):
    return AppendRequest(stream_id=stream, event_type=EventType.RESULT, payload_type=PayloadType.IMAGE,
                         actor_kind=ActorKind.TOOL, actor_id=uuid.UUID(int=9), source=Source.TOOL,
                         idempotency_key=str(uuid.uuid4()), content=None, attachment=data,
                         attachment_media_type="image/png", attachment_description="screenshot")


@pytest.fixture
def blobs(tmp_path):
    return LocalDiskBlobStore(tmp_path / "blobs")


@pytest.fixture
def rw(session, streams):
    p = uuid.uuid4()
    return lambda: session(p, read=[streams["a"]], write=[streams["a"]])


def gc(streams, blobs, **kw):
    with psycopg.connect(streams["dsn"]["gc"]) as c:
        return collect_orphan_blobs(c, blobs, **kw)


def age(blobs, ref, seconds):
    path = blobs._path(ref)
    t = time.time() - seconds
    os.utime(path, (t, t))


def orphan(rw, provider, streams, blobs, data=PNG):
    """A blob whose append rolled back. The data key is created first in a committed append, as in real use;
    otherwise the rollback would also drop the key and the orphan could never be deduplicated onto."""
    with rw() as s:
        append_event(s, provider, req(streams["a"], b"prime the key " + os.urandom(8).hex().encode()), blob_store=blobs)
    with pytest.raises(RuntimeError):
        with rw() as s:
            ref = append_event(s, provider, req(streams["a"], data), blob_store=blobs).envelope.attachment_ref
            raise RuntimeError("append rolled back after the blob was written")
    return ref


def test_an_old_orphan_is_deleted_and_a_young_one_kept(rw, provider, streams, blobs):
    old, young = orphan(rw, provider, streams, blobs), orphan(rw, provider, streams, blobs, PNG + b"2")
    age(blobs, old, DAY + 60)
    age(blobs, young, DAY - 60)                                           # 23 h 59 min: still protected
    r = gc(streams, blobs)
    assert (r.deleted, r.skipped_young) == (1, 3)                        # + the two young priming blobs
    assert not blobs.exists(old) and blobs.exists(young)


def test_a_referenced_blob_is_kept_even_when_old(rw, provider, streams, blobs):
    with rw() as s:
        env = append_event(s, provider, req(streams["a"]), blob_store=blobs).envelope
    age(blobs, env.attachment_ref, 30 * DAY)
    assert gc(streams, blobs).kept_referenced == 1
    with rw() as s:
        assert read_attachment(s, provider, blobs, env) == PNG


def test_a_blob_of_a_shredded_event_is_kept(rw, provider, streams, blobs):
    # Amendment 4: erasure is final by key destruction; the blob is still referenced and is not collected.
    with rw() as s:
        env = append_event(s, provider, req(streams["a"]), blob_store=blobs).envelope
    with psycopg.connect(streams["dsn"]["admin"]) as c:
        c.execute("DELETE FROM keys.stream_master_keys WHERE stream_id = %s", (streams["a"],))
    age(blobs, env.attachment_ref, 30 * DAY)
    r = gc(streams, blobs)
    assert r.deleted == 0 and blobs.exists(env.attachment_ref)


def test_an_append_reusing_an_old_orphan_blocks_its_collection(rw, provider, streams, blobs):
    # The owner's race: the append dedups onto an aged orphan and holds the shared lock until commit.
    ref = orphan(rw, provider, streams, blobs)
    age(blobs, ref, 30 * DAY)
    with rw() as s:
        env = append_event(s, provider, req(streams["a"]), blob_store=blobs).envelope   # dedup: no new write
        assert env.attachment_ref == ref
        r = gc(streams, blobs)                                            # runs while the append is uncommitted
        assert (r.deleted, r.skipped_locked) == (0, 1)
    with rw() as s:
        assert read_attachment(s, provider, blobs, env) == PNG            # the event never points to a missing file
    assert gc(streams, blobs).kept_referenced == 1


def test_an_append_waits_for_a_collection_in_progress_then_rewrites_the_blob(rw, provider, streams, blobs):
    # The other order: the collector holds the exclusive lock; the append's existence check waits for it.
    ref = orphan(rw, provider, streams, blobs)
    with psycopg.connect(streams["dsn"]["gc"]) as c, c.transaction():
        assert c.execute("SELECT pg_try_advisory_xact_lock(ledger.attachment_lock_namespace(), "
                         "ledger.attachment_lock_key(%s))", (ref,)).fetchone()[0]
        done = []
        t = threading.Thread(target=lambda: done.append(_append(rw, provider, streams, blobs)))
        t.start()
        time.sleep(0.5)
        assert not done                                                   # blocked on the lock
        blobs.delete(ref)                                                 # the collector's deletion
    t.join(10)
    (env,) = done
    with rw() as s:
        assert read_attachment(s, provider, blobs, env) == PNG            # rewritten after the lock was released


def _append(rw, provider, streams, blobs):
    with rw() as s:
        return append_event(s, provider, req(streams["a"]), blob_store=blobs).envelope


def test_stale_temp_files_are_removed_and_fresh_ones_kept(blobs):
    (blobs._root / "ab").mkdir()
    stale, fresh = blobs._root / "ab" / ".x.1.tmp", blobs._root / "ab" / ".x.2.tmp"
    for p in (stale, fresh):
        p.write_bytes(b"partial")
    t = time.time() - DAY - 60
    os.utime(stale, (t, t))
    assert blobs.remove_stale_temp(DAY) == 1
    assert not stale.exists() and fresh.exists()


def test_the_collector_refuses_other_roles_and_a_shorter_age(streams, blobs):
    with psycopg.connect(streams["dsn"]["app"]) as c:
        with pytest.raises(CollectionError, match="nacre_gc"):
            collect_orphan_blobs(c, blobs)
    with pytest.raises(CollectionError, match="below"):
        gc(streams, blobs, min_age=timedelta(hours=1))


def test_an_event_committed_after_the_prefilter_is_seen_by_the_locked_check(rw, provider, streams, blobs, monkeypatch):
    # The pre-filter is only an optimisation: the decision is the check made under the exclusive lock.
    ref = orphan(rw, provider, streams, blobs)
    age(blobs, ref, 30 * DAY)
    listed = blobs.list_refs

    def list_then_commit_a_reference():
        refs = list(listed())                                             # the pre-filter has already run
        _append(rw, provider, streams, blobs)                             # dedups onto the orphan and commits
        return iter(refs)
    monkeypatch.setattr(blobs, "list_refs", list_then_commit_a_reference)
    r = gc(streams, blobs)
    assert r.deleted == 0 and blobs.exists(ref)


def test_the_collector_removes_stale_temp_files(streams, blobs):
    (blobs._root / "cd").mkdir()
    stale = blobs._root / "cd" / ".y.1.tmp"
    stale.write_bytes(b"partial")
    t = time.time() - DAY - 60
    os.utime(stale, (t, t))
    assert gc(streams, blobs).temp_removed == 1 and not stale.exists()
