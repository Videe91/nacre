"""
Functionality: Verify every stream's seal chain and its signed checkpoints, without any key.
Owns: recomputing seals, checking gapless sequence and prev_hash linkage, checking that each chain still passes
  through every witnessed checkpoint, verifying checkpoint signatures, and reconciling DB checkpoints with the
  witness file.
Public entry: verify_chain(), ChainReport
Decisions: D-0002, D-0003, D-0005, D-0013
Assumptions: A-0013, A-0020
Notes: Runs on a `nacre_verifier` connection (reads all events and checkpoints, never keys; D-0005 C-5). It needs no
  key because seals cover ciphertext (D-0003), so shredded streams still verify.
  Trust anchor = the witness file plus the public keys the CALLER configures (`trusted_keys`). Public keys are
  never taken from the database (migration 0004 note). Findings:
    problem  seal/linkage/gap mismatch; checkpoint not on the chain; bad or unknown-key signature; DB checkpoint
             row missing from the witness or different from it (tampering);
    warning  a witness entry with no DB row (possible crash between the witness write and the commit).
  A rewritten stream recomputes cleanly, but its hash at a witnessed commit_seq no longer equals the signed
  head_hash: that is how a full-stream rewrite is caught (gate item 3).
"""
import json
from dataclasses import dataclass, field, fields
from datetime import datetime
from pathlib import Path
from uuid import UUID

import psycopg
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from nacre.core.event import Envelope
from nacre.ledger.read_stream import envelope_from_row
from nacre.ledger.seal_event import GENESIS_PREV_HASH, seal_event
from nacre.ledger.write_checkpoint import signed_message

_COLUMNS = [f.name for f in fields(Envelope)]


@dataclass
class ChainReport:
    streams_checked: int = 0
    events_checked: int = 0
    checkpoints_checked: int = 0
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def verify_chain(conn: psycopg.Connection, witness: Path, trusted_keys: dict[str, bytes]) -> ChainReport:
    """Verify all streams visible to the verifier role against the witness file and the trusted keys."""
    report = ChainReport()
    hashes: dict[UUID, dict[int, bytes]] = {}
    current, expected_seq, prev = None, 0, GENESIS_PREV_HASH
    for row in conn.execute(f"SELECT {', '.join(_COLUMNS)} FROM ledger.events ORDER BY stream_id, commit_seq"):
        env = envelope_from_row(row)
        if env.stream_id != current:
            current, expected_seq, prev = env.stream_id, 1, GENESIS_PREV_HASH
            report.streams_checked += 1
            hashes[current] = {}
        report.events_checked += 1
        where = f"stream {env.stream_id} seq {env.commit_seq}"
        if env.commit_seq != expected_seq:
            report.problems.append(f"{where}: expected seq {expected_seq} (gap, deletion or reordering)")
        if env.prev_hash != prev:
            report.problems.append(f"{where}: prev_hash does not link to the previous event")
        values = {k: getattr(env, k) for k in _COLUMNS if k != "hash"}
        if seal_event(values) != env.hash:
            report.problems.append(f"{where}: seal does not match its contents (edited)")
        hashes[current][env.commit_seq] = env.hash
        expected_seq, prev = env.commit_seq + 1, env.hash
    conn.commit()
    witnessed = _check_witness(witness, trusted_keys, hashes, report)
    _reconcile_db_checkpoints(conn, witnessed, report)
    return report


def _check_witness(witness, trusted_keys, hashes, report):
    witnessed = {}
    lines = Path(witness).read_text().splitlines() if Path(witness).exists() else []
    for n, line in enumerate(lines, 1):
        rec = json.loads(line)
        report.checkpoints_checked += 1
        sid, seq, head = UUID(rec["stream_id"]), rec["commit_seq"], bytes.fromhex(rec["head_hash"])
        where = f"witness line {n} (stream {sid} seq {seq})"
        key = trusted_keys.get(rec["signing_key_id"])
        if key is None:
            report.problems.append(f"{where}: signed by an untrusted or unknown key {rec['signing_key_id']}")
        else:
            msg = signed_message(sid, seq, head, datetime.fromisoformat(rec["signed_at"]), rec["signing_key_id"])
            try:
                Ed25519PublicKey.from_public_bytes(key).verify(bytes.fromhex(rec["signature"]), msg)
            except InvalidSignature:
                report.problems.append(f"{where}: signature does not verify")
        if hashes.get(sid, {}).get(seq) != head:
            report.problems.append(f"{where}: the chain no longer passes through this checkpoint (rewritten or truncated)")
        witnessed[rec["checkpoint_id"]] = rec
    return witnessed


def _reconcile_db_checkpoints(conn, witnessed, report):
    rows = conn.execute("SELECT checkpoint_id, stream_id, commit_seq, head_hash, signing_key_id, signature "
                        "FROM ledger.checkpoints").fetchall()
    conn.commit()
    seen = set()
    for cid, sid, seq, head, kid, sig in rows:
        rec = witnessed.get(str(cid))
        seen.add(str(cid))
        if rec is None:
            report.problems.append(f"DB checkpoint {cid} (stream {sid} seq {seq}) is not in the witness (tampering)")
        elif (str(sid), seq, bytes(head).hex(), kid, bytes(sig).hex()) != (
                rec["stream_id"], rec["commit_seq"], rec["head_hash"], rec["signing_key_id"], rec["signature"]):
            report.problems.append(f"DB checkpoint {cid} differs from its witness entry (tampering)")
    for cid in set(witnessed) - seen:
        report.warnings.append(f"witness checkpoint {cid} has no DB row (possible crash between witness write and commit)")
