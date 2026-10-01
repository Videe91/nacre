"""Tests for recall/index_version.py (R9, D-0024): one encrypted entry per version, in the version's transaction,
under the version key's recall_index sub-key; nothing readable in any column; bound to its own id, generation and
embedder; unreadable once the key is destroyed; a different embedder refuses the write."""
import uuid

import numpy as np
import psycopg
import pytest

from recall_kit import belief
from nacre.keys.decrypt_payload import DecryptError
from nacre.keys.get_or_create_key import load_key
from nacre.recall.embed_local import DIM, EMBEDDER_ID
from nacre.recall.index_version import IndexEmbedderMismatch, default_embedder, open_entry

CORR = "Pin the payments base image by digest before every release."
NUC = "Pin the payments base image by digest"


def _rows(s):
    return s.conn.execute("SELECT stream_id, index_generation, version_event_id, key_id, embedder_id, body "
                          "FROM recall.index_entries ORDER BY seq").fetchall()


def _open(s, provider, row, **over):
    stream, gen, vid, key_id, emb, body = row
    args = dict(stream_id=stream, generation=gen, version_event_id=vid, embedder_id=emb, body=bytes(body))
    args.update(over)
    return open_entry(load_key(s.conn, provider, key_id), dim=DIM, **args)


def test_each_version_gets_one_entry_that_opens_to_its_nucleus_and_embedding(rw, provider, streams):
    with rw() as s:
        b = belief(s, provider, streams["a"], CORR, NUC)
        rows = _rows(s)
        vid, key_id = s.conn.execute("SELECT event_id, (SELECT key_id FROM ledger.events WHERE event_id = v.event_id) "
                                     "FROM interp.versions v WHERE object_id = %s", (b.object_id,)).fetchone()
        assert [(r[2], r[3], r[4]) for r in rows] == [(vid, key_id, EMBEDDER_ID)]
        e = _open(s, provider, rows[0])
    assert (e.text, e.kind, e.addresses) == (NUC, "belief", ())
    assert e.embedding.shape == (DIM,) and float(e.embedding @ default_embedder().embed([NUC])[0]) > 0.99999


def test_no_column_holds_the_text_or_the_embedding_bytes(rw, provider, streams):
    with rw() as s:
        belief(s, provider, streams["a"], CORR, NUC)
        dump = b"".join(bytes(c) if isinstance(c, (bytes, memoryview)) else str(c).encode()
                        for r in _rows(s) for c in r)
    vec = np.ascontiguousarray(default_embedder().embed([NUC])[0], dtype="<f4").tobytes()
    assert NUC.encode() not in dump and NUC.casefold().encode() not in dump
    assert vec[:16] not in dump   # not even a 4-dimension run of the plaintext vector


def test_the_entry_rolls_back_with_its_version(rw, provider, streams):
    with pytest.raises(RuntimeError), rw() as s:
        belief(s, provider, streams["a"], CORR, NUC)
        raise RuntimeError("abort")
    with rw() as s:
        assert _rows(s) == [] and s.conn.execute("SELECT count(*) FROM interp.versions").fetchone()[0] == 0


@pytest.mark.parametrize("field", ["version_event_id", "generation", "embedder_id", "stream_id"])
def test_an_entry_is_bound_to_its_own_id_generation_embedder_and_stream(rw, provider, streams, field):
    with rw() as s:
        belief(s, provider, streams["a"], CORR, NUC)
        row = _rows(s)[0]
        wrong = {"version_event_id": uuid.uuid4(), "generation": 2, "embedder_id": "other@1#x",
                 "stream_id": streams["b"]}[field]
        with pytest.raises(DecryptError):
            _open(s, provider, row, **{field: wrong})


def test_a_tampered_body_or_a_different_key_is_refused(rw, provider, streams):
    with rw() as s:
        belief(s, provider, streams["a"], CORR, NUC)
        belief(s, provider, streams["a"], "Run the migration check before merging schema changes.",
               "Run the migration check")
        r1, r2 = _rows(s)
        body = bytearray(r1[5])
        body[-1] ^= 1
        with pytest.raises(DecryptError):
            _open(s, provider, r1, body=bytes(body))
        if r1[3] != r2[3]:                                   # different contributor-set keys: header mismatch
            with pytest.raises(DecryptError):
                open_entry(load_key(s.conn, provider, r2[3]), r1[0], r1[1], r1[2], r1[4], bytes(r1[5]), DIM)


def test_destroying_the_version_key_makes_the_entry_unreadable(rw, provider, streams, migrated_db):
    with rw() as s:
        belief(s, provider, streams["a"], CORR, NUC)
        key_id = _rows(s)[0][3]
    with psycopg.connect(migrated_db["keyadmin"]) as ka:
        ka.execute("DELETE FROM keys.data_keys WHERE key_id = %s", (key_id,))
    with rw() as s:
        assert load_key(s.conn, provider, key_id) is None    # nothing left to open the entry with
        assert len(_rows(s)) == 1                            # the ciphertext row stays (append-only); it is noise


def test_a_stream_indexed_with_another_embedder_refuses_new_versions(rw, provider, streams):
    with rw() as s:
        s.conn.execute("INSERT INTO recall.index_generations (stream_id, generation, embedder_id) VALUES (%s, 1, %s)",
                       (streams["a"], "older-model@0#abc"))
    with pytest.raises(IndexEmbedderMismatch), rw() as s:
        belief(s, provider, streams["a"], CORR, NUC)
    with rw() as s:
        assert s.conn.execute("SELECT count(*) FROM interp.versions").fetchone()[0] == 0   # version rolled back too
