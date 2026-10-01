"""Tests for stores/write_version.py: event + rows atomically, keyed MACs, no plaintext in the projection."""
import hashlib

import pytest

from stores_kit import RULE, grounded_belief
from nacre.core.encode_cbor import encode_cbor
from nacre.stores.write_version import read_version_events


def test_the_projection_holds_keyed_macs_not_plaintext_or_plain_hashes(rw, provider, streams):
    a = streams["a"]
    with rw() as s:
        b = grounded_belief(s, provider, a)
        (v,) = read_version_events(s, provider, a)
        row = s.conn.execute("SELECT content_mac FROM interp.versions WHERE object_id = %s", (b.object_id,)).fetchone()
        spans = [bytes(r[0]) for r in s.conn.execute(
            "SELECT span_mac FROM interp.edges WHERE object_id = %s AND span_mac IS NOT NULL", (b.object_id,))]
        dump = b"".join(bytes(r[0]) if r[0] else b"" for r in s.conn.execute(
            "SELECT content_mac FROM interp.versions UNION ALL SELECT span_mac FROM interp.edges"))
    assert bytes(row[0]) != hashlib.sha256(encode_cbor(v.body["content"])).digest()       # keyed, not a plain hash
    assert spans and all(m != hashlib.sha256(RULE.encode()).digest() for m in spans)
    assert RULE.encode() not in dump


def test_event_and_rows_commit_together(rw, provider, streams):
    a = streams["a"]
    with pytest.raises(RuntimeError):
        with rw() as s:
            grounded_belief(s, provider, a)
            raise RuntimeError("abort after writing")
    with rw() as s:
        assert s.conn.execute("SELECT count(*) FROM interp.versions").fetchone()[0] == 0
        assert read_version_events(s, provider, a) == []
