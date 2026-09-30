"""Tests for ledger/verify_chain.py: the tamper suite (Phase 1 gate item 3) and chain validity after shredding (gate 4)."""
import json
import uuid
from dataclasses import fields
from datetime import UTC, datetime

import psycopg
import pytest

from nacre.core.event import Envelope
from nacre.ledger.read_stream import envelope_from_row
from nacre.ledger.seal_event import GENESIS_PREV_HASH, seal_event
from nacre.ledger.verify_chain import verify_chain
from nacre.ledger.write_checkpoint import LocalFileSigner, write_checkpoints

T0 = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
COLS = [f.name for f in fields(Envelope)]


@pytest.fixture
def checkpointed(filled, streams, signer, witness):
    """Stream 'a' with 5 events and a signed checkpoint at seq 5."""
    filled(5)
    with psycopg.connect(streams["dsn"]["checkpointer"]) as c:
        write_checkpoints(c, signer, witness, now=T0)
    return {signer.key_id: signer.public_key_bytes()}


def _verify(streams, witness, keys):
    with psycopg.connect(streams["dsn"]["verifier"]) as c:
        return verify_chain(c, witness, keys)


def _tamper(streams, *sql):
    with psycopg.connect(streams["dsn"]["admin"]) as c:
        c.execute("ALTER TABLE ledger.events DISABLE TRIGGER events_no_update_delete")
        c.execute("ALTER TABLE ledger.events DISABLE TRIGGER events_check_linkage")
        for stmt, params in sql:
            c.execute(stmt, params)
        c.execute("ALTER TABLE ledger.events ENABLE TRIGGER events_no_update_delete")
        c.execute("ALTER TABLE ledger.events ENABLE TRIGGER events_check_linkage")


def test_a_clean_ledger_verifies(checkpointed, streams, witness):
    r = _verify(streams, witness, checkpointed)
    assert r.ok and r.events_checked == 5 and r.checkpoints_checked == 1 and r.warnings == []


@pytest.mark.parametrize("name,sql", [
    ("edited ciphertext", ("UPDATE ledger.events SET body_ciphertext = body_ciphertext || '\\x00'::bytea WHERE commit_seq = 3", ())),
    ("edited header", ("UPDATE ledger.events SET actor_model = 'forged-model' WHERE commit_seq = 2", ())),
    ("deleted row", ("DELETE FROM ledger.events WHERE commit_seq = 3", ())),
    ("reordered rows", ("UPDATE ledger.events SET commit_seq = CASE commit_seq WHEN 2 THEN 4 WHEN 4 THEN 2 END "
                        "WHERE commit_seq IN (2, 4)", ())),
])
def test_tampering_is_detected(checkpointed, streams, witness, name, sql):
    if name == "reordered rows":
        _tamper(streams, ("ALTER TABLE ledger.events DROP CONSTRAINT events_stream_id_commit_seq_key", ()), sql)
    else:
        _tamper(streams, sql)
    assert not _verify(streams, witness, checkpointed).ok, name


def test_a_full_stream_rewrite_with_recomputed_hashes_is_caught_only_by_the_checkpoint(checkpointed, streams, witness):
    # Gate item 3: the attacker rewrites every event and recomputes every seal; the chain is self-consistent.
    with psycopg.connect(streams["dsn"]["admin"]) as c:
        rows = [envelope_from_row(r) for r in c.execute(
            f"SELECT {', '.join(COLS)} FROM ledger.events WHERE stream_id = %s ORDER BY commit_seq", (streams["a"],))]
    prev, forged = GENESIS_PREV_HASH, []
    for env in rows:
        values = {k: getattr(env, k) for k in COLS if k != "hash"}
        values.update(body_ciphertext=env.body_ciphertext[:-1] + bytes([env.body_ciphertext[-1] ^ 1]), prev_hash=prev)
        values["hash"] = seal_event(values)
        prev = values["hash"]
        forged.append(values)
    stmts = [("DELETE FROM ledger.events WHERE stream_id = %s", (streams["a"],))]
    stmts += [(f"INSERT INTO ledger.events ({', '.join(COLS)}) VALUES ({', '.join(['%s'] * len(COLS))})",
               [v[c] for c in COLS]) for v in forged]
    _tamper(streams, *stmts)
    report = _verify(streams, witness, checkpointed)
    assert not report.ok
    assert all("checkpoint" in p for p in report.problems)            # seals and linkage all look fine


def test_shredding_keeps_the_chain_valid(checkpointed, streams, witness):
    # Gate item 4 (chain side): destroying keys makes payloads unreadable but never breaks verification.
    with psycopg.connect(streams["dsn"]["admin"]) as c:
        c.execute("DELETE FROM keys.stream_master_keys WHERE stream_id = %s", (streams["a"],))
    assert _verify(streams, witness, checkpointed).ok


def test_checkpoint_forgeries_and_mismatches(checkpointed, streams, witness, tmp_path):
    rogue = LocalFileSigner.initialise(tmp_path / "rogue.key")
    assert not _verify(streams, witness, {rogue.key_id: rogue.public_key_bytes()}).ok       # untrusted key
    lines = witness.read_text().splitlines()
    rec = json.loads(lines[0])
    rec["signature"] = ("00" if rec["signature"][:2] != "00" else "11") + rec["signature"][2:]
    witness.write_text(json.dumps(rec) + "\n")
    assert any("signature" in p for p in _verify(streams, witness, checkpointed).problems)


def test_db_checkpoint_missing_from_witness_is_tampering_and_the_reverse_is_a_warning(checkpointed, streams, witness):
    witness.write_text("")
    r = _verify(streams, witness, checkpointed)
    assert any("not in the witness" in p for p in r.problems)
    with psycopg.connect(streams["dsn"]["admin"]) as c:
        c.execute("ALTER TABLE ledger.checkpoints DISABLE TRIGGER checkpoints_no_update_delete")
        c.execute("DELETE FROM ledger.checkpoints")
        c.execute("ALTER TABLE ledger.checkpoints ENABLE TRIGGER checkpoints_no_update_delete")
    witness.write_text("")
    with psycopg.connect(streams["dsn"]["checkpointer"]) as c:
        write_checkpoints(c, LocalFileSigner(witness.parent / "ckpt" / "signing.key"), witness, now=T0)
    with psycopg.connect(streams["dsn"]["admin"]) as c:
        c.execute("ALTER TABLE ledger.checkpoints DISABLE TRIGGER checkpoints_no_update_delete")
        c.execute("DELETE FROM ledger.checkpoints")
        c.execute("ALTER TABLE ledger.checkpoints ENABLE TRIGGER checkpoints_no_update_delete")
    r = _verify(streams, witness, checkpointed)
    assert r.ok and any("no DB row" in w for w in r.warnings)


def test_the_verifier_needs_no_keys(checkpointed, streams, witness):
    with psycopg.connect(streams["dsn"]["verifier"]) as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("SELECT 1 FROM keys.data_keys")


def test_hiding_the_db_checkpoint_does_not_hide_a_rewrite(checkpointed, streams, witness):
    # Owner: a missing DB checkpoint row may be an attacker hiding a rewrite, not crash debris. The chain is
    # checked against EVERY witness entry, with or without a DB row.
    with psycopg.connect(streams["dsn"]["admin"]) as c:
        c.execute("ALTER TABLE ledger.checkpoints DISABLE TRIGGER checkpoints_no_update_delete")
        c.execute("DELETE FROM ledger.checkpoints")
        c.execute("ALTER TABLE ledger.checkpoints ENABLE TRIGGER checkpoints_no_update_delete")
        rows = [envelope_from_row(r) for r in c.execute(
            f"SELECT {', '.join(COLS)} FROM ledger.events WHERE stream_id = %s ORDER BY commit_seq", (streams["a"],))]
    prev, forged = GENESIS_PREV_HASH, []
    for env in rows:
        values = {k: getattr(env, k) for k in COLS if k != "hash"}
        values.update(body_ciphertext=env.body_ciphertext + b"\x00", prev_hash=prev)
        values["hash"] = seal_event(values)
        prev = values["hash"]
        forged.append(values)
    _tamper(streams, ("DELETE FROM ledger.events WHERE stream_id = %s", (streams["a"],)),
            *[(f"INSERT INTO ledger.events ({', '.join(COLS)}) VALUES ({', '.join(['%s'] * len(COLS))})",
               [v[c] for c in COLS]) for v in forged])
    report = _verify(streams, witness, checkpointed)
    assert not report.ok
    assert any("no longer passes through this checkpoint" in p for p in report.problems)
    assert any("no DB row" in w for w in report.warnings)
