"""Tests for ledger/write_checkpoint.py (D-0003, D-0013): message format, key file, cadence, DB + witness."""
import json
import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from nacre.ledger.write_checkpoint import LocalFileSigner, signed_message, write_checkpoints

T0 = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)


def test_signed_message_is_frozen():
    msg = signed_message(uuid.UUID(int=1), 7, b"\x02" * 32, datetime(2026, 9, 30, tzinfo=UTC), "ed25519:0123456789abcdef")
    assert msg.hex() == FROZEN_MESSAGE


def test_signer_file_is_owner_only_and_key_id_names_the_key(tmp_path):
    s = LocalFileSigner.initialise(tmp_path / "k")
    assert s.key_id.startswith("ed25519:") and len(s.key_id) == 24
    (tmp_path / "k").chmod(0o640)
    with pytest.raises(PermissionError):
        LocalFileSigner(tmp_path / "k")


def _ckpt(streams):
    return psycopg.connect(streams["dsn"]["checkpointer"])


def test_first_round_checkpoints_every_stream_then_only_when_due(filled, streams, signer, witness):
    filled(3)
    with _ckpt(streams) as c:
        first = write_checkpoints(c, signer, witness, now=T0)
        assert [r["commit_seq"] for r in first] == [3]
        assert write_checkpoints(c, signer, witness, now=T0 + timedelta(minutes=5)) == []        # nothing new
    filled(2)
    with _ckpt(streams) as c:
        assert write_checkpoints(c, signer, witness, now=T0 + timedelta(minutes=10)) == []      # new, but < 1 h
        assert [r["commit_seq"] for r in write_checkpoints(c, signer, witness, now=T0 + timedelta(minutes=61))] == [5]
    filled(3)
    with _ckpt(streams) as c:
        assert [r["commit_seq"] for r in write_checkpoints(c, signer, witness, now=T0 + timedelta(minutes=62),
                                                           every_events=3)] == [8]            # event-count rule


def test_db_row_and_witness_line_agree(filled, streams, signer, witness):
    filled(2)
    with _ckpt(streams) as c:
        (rec,) = write_checkpoints(c, signer, witness, now=T0)
    (line,) = witness.read_text().splitlines()
    assert json.loads(line) == rec and rec["signing_key_id"] == signer.key_id
    with psycopg.connect(streams["dsn"]["admin"]) as c:
        row = c.execute("SELECT commit_seq, head_hash, signing_key_id FROM ledger.checkpoints").fetchone()
    assert (row[0], bytes(row[1]).hex(), row[2]) == (rec["commit_seq"], rec["head_hash"], rec["signing_key_id"])


FROZEN_MESSAGE = "6e616372652d636865636b706f696e742d7631000000000000000000000000000000010000000000000007020202020202020202020202020202020202020202020202020202020202020200065ca7faf640000018656432353531393a30313233343536373839616263646566"  # frozen 2026-09-30
