"""Tests for recall/rebuild_index.py (R11, D-0024): an identical rebuild writes nothing; a damaged or missing entry
and an embedder change each produce generation g+1 and a switch, atomically; shredded versions are unverifiable and
get no new entry; generation 1 is back-filled for a stream with none; the append lock is held throughout."""
import threading
import time
import uuid

import numpy as np
import psycopg
import pytest

from recall_kit import belief
from nacre.recall.embed_local import DIM
from nacre.recall.index_version import default_embedder
from nacre.recall.load_index_cache import IndexCache
from nacre.recall.rebuild_index import rebuild_index

A1 = ("Pin the payments base image by digest before release.", "Pin the payments base image by digest")
A2 = ("Run the schema migration check before merging.", "Run the schema migration check")


class OtherEmbedder:
    """A different model: same dimension, different vectors and id."""
    embedder_id = "test-other-model@1#0000"
    dim = DIM

    def embed(self, texts):
        base = default_embedder().embed(texts)
        out = base[:, ::-1].copy()
        return (out / np.linalg.norm(out, axis=1, keepdims=True)).astype(np.float32)


def _gens(s, stream):
    return s.conn.execute("SELECT index_generation, count(*) FROM recall.index_entries WHERE stream_id = %s GROUP BY 1 "
                          "ORDER BY 1", (stream,)).fetchall()


@pytest.fixture
def two(rw, provider, streams):
    with rw() as s:
        belief(s, provider, streams["a"], *A1, person=uuid.UUID(int=31))
        belief(s, provider, streams["a"], *A2, person=uuid.UUID(int=32))


def test_an_identical_rebuild_writes_nothing(two, rw, provider, streams):
    with rw() as s:
        c = rebuild_index(s, provider, streams["a"])
        assert c.identical and c.versions == 2 and c.switched_to is None
        assert _gens(s, streams["a"]) == [(1, 2)]


def test_an_embedder_change_reindexes_fully_into_a_new_generation(two, rw, snap, provider, streams):
    with rw() as s:
        c = rebuild_index(s, provider, streams["a"], OtherEmbedder())
        assert (c.reason, c.switched_to) == ("embedder_change", 2) and not c.identical
        assert _gens(s, streams["a"]) == [(1, 2), (2, 2)]
        assert s.conn.execute("SELECT embedder_id FROM recall.index_generations WHERE stream_id = %s AND generation = 2",
                              (streams["a"],)).fetchone()[0] == OtherEmbedder.embedder_id
    with snap() as s:                                    # recall now sees only generation 2's vectors
        heads = [r[0] for r in s.conn.execute("SELECT event_id FROM interp.heads")]
        got = IndexCache(provider, dim=DIM).entries(s, {streams["a"]: heads})[streams["a"]]
        want = OtherEmbedder().embed([e.text for e in got.values()])
        assert np.allclose(np.vstack([e.embedding for e in got.values()]), want, atol=1e-6)


def test_writes_after_an_embedder_change_need_the_new_embedder(two, rw, provider, streams):
    from nacre.recall.index_version import IndexEmbedderMismatch
    with rw() as s:
        rebuild_index(s, provider, streams["a"], OtherEmbedder())
    with pytest.raises(IndexEmbedderMismatch), rw() as s:   # the default (old) model may no longer write
        belief(s, provider, streams["a"], "Rotate the staging certificate weekly 2026.", "Rotate the staging certificate")


def test_a_missing_entry_is_a_difference_and_triggers_a_rebuild_generation(rw, provider, streams, migrated_db):
    with rw() as s:
        belief(s, provider, streams["a"], *A1)
    with psycopg.connect(migrated_db["admin"]) as admin:   # simulate a lost entry (bypassing append-only, test only)
        admin.execute("ALTER TABLE recall.index_entries DISABLE TRIGGER index_entries_no_update_delete")
        admin.execute("SET ROLE nacre_migrator")
        admin.execute("ALTER TABLE recall.index_entries NO FORCE ROW LEVEL SECURITY")
        admin.execute("DELETE FROM recall.index_entries")
        admin.execute("ALTER TABLE recall.index_entries FORCE ROW LEVEL SECURITY")
        admin.execute("RESET ROLE")
        admin.execute("ALTER TABLE recall.index_entries ENABLE TRIGGER index_entries_no_update_delete")
    with rw() as s:
        c = rebuild_index(s, provider, streams["a"])
        assert (c.reason, c.switched_to, len(c.differences)) == ("rebuild", 2, 1)
        assert _gens(s, streams["a"]) == [(2, 1)]


def test_shredded_versions_are_unverifiable_and_get_no_new_entry(two, rw, provider, streams, migrated_db):
    with rw() as s:
        key = s.conn.execute("SELECT key_id FROM recall.index_entries ORDER BY seq LIMIT 1").fetchone()[0]
    with psycopg.connect(migrated_db["keyadmin"]) as ka:
        ka.execute("DELETE FROM keys.data_keys WHERE key_id = %s", (key,))
    with rw() as s:
        c = rebuild_index(s, provider, streams["a"], OtherEmbedder())
        assert len(c.unverifiable) == 1 and c.switched_to == 2
        assert _gens(s, streams["a"]) == [(1, 2), (2, 1)]


def test_generation_one_is_backfilled_for_a_stream_with_no_index(rw, provider, streams, migrated_db):
    with rw() as s:
        belief(s, provider, streams["a"], *A1)
    with psycopg.connect(migrated_db["admin"]) as admin:   # a stream written before the index existed (test only)
        admin.execute("SET ROLE nacre_migrator")
        for t in ("index_entries", "index_generations"):
            admin.execute(f"ALTER TABLE recall.{t} DISABLE TRIGGER USER")
            admin.execute(f"ALTER TABLE recall.{t} NO FORCE ROW LEVEL SECURITY")
        admin.execute("DELETE FROM recall.index_entries")
        admin.execute("DELETE FROM recall.index_generations")
        for t in ("index_entries", "index_generations"):
            admin.execute(f"ALTER TABLE recall.{t} FORCE ROW LEVEL SECURITY")
            admin.execute(f"ALTER TABLE recall.{t} ENABLE TRIGGER USER")
    with rw() as s:
        c = rebuild_index(s, provider, streams["a"])
        assert c.backfilled and c.switched_to is None and _gens(s, streams["a"]) == [(1, 1)]


def test_the_rebuild_holds_the_append_lock_so_no_version_lands_mid_rebuild(two, rw, provider, streams):
    done = {}
    with rw() as s:
        assert rebuild_index(s, provider, streams["a"]).identical      # same embedder: the writer stays valid

        def writer():
            with rw() as w:
                belief(w, provider, streams["a"], "Never deploy on Friday 2026.", "Never deploy on Friday")
                done["at"] = time.monotonic()
        t = threading.Thread(target=writer)
        t.start()
        time.sleep(0.5)
        held_until = time.monotonic()
        assert "at" not in done                         # blocked on the stream lock while the rebuild is open
    t.join(10)
    assert done.get("at", 0) >= held_until
