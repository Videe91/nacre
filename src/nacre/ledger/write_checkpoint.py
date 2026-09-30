"""
Functionality: The checkpointer: sign the heads of streams that are due, into ledger.checkpoints and the witness file.
Owns: the Ed25519 signing-key file, the signed message format, the cadence rule, and the witness JSON Lines.
Public entry: write_checkpoints(), LocalFileSigner, signed_message()
Decisions: D-0003, D-0005, D-0013
Assumptions: A-0013, A-0020
Notes: Runs as its own process on a `nacre_checkpointer` connection (D-0005 amendment 2: it reads only stream_id,
  commit_seq and hash, and may insert checkpoints). The signing key never enters the database (C-4).
  Signed message (D-0013), with signing_key_id carrying the key version (owner addition):
    "nacre-checkpoint-v1" | stream_id (16) | commit_seq (u64 BE) | head_hash (32) | signed_at (i64 BE µs UTC)
    | len(signing_key_id) (u16 BE) | signing_key_id (UTF-8)
  signing_key_id = "ed25519:" + first 16 hex of SHA-256(raw public key).
  Cadence (D-0003): a stream is due when it has >= every_events new events since its last checkpoint, or at least
  one new event and every_seconds elapsed since that checkpoint. A stream never checkpointed is due as soon as it
  has an event (it has no "last signed" time; the checkpointer cannot read event times, C-1). (D1)
  Order inside one DB transaction: INSERT the row, append + fsync the witness line, COMMIT. A crash after the
  witness write leaves a witness entry with no DB row (verify_chain reports it as a warning). A DB row with no
  witness entry is reported as tampering, because the witness is the anchor.
"""
import hashlib
import json
import os
import secrets
import stat
import struct
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

PREFIX = b"nacre-checkpoint-v1"
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def signed_message(stream_id: uuid.UUID, commit_seq: int, head_hash: bytes, signed_at: datetime, signing_key_id: str) -> bytes:
    kid = signing_key_id.encode()
    micros = (signed_at - _EPOCH) // timedelta(microseconds=1)
    return (PREFIX + stream_id.bytes + struct.pack(">Q", commit_seq) + head_hash + struct.pack(">q", micros)
            + struct.pack(">H", len(kid)) + kid)


class LocalFileSigner:
    """Ed25519 signing key in a 0600 file (32-byte raw seed), held only by the checkpointer process."""

    def __init__(self, path: Path):
        path = Path(path)
        if stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise PermissionError(f"{path} is readable by group or others; chmod 600 it")
        seed = path.read_bytes()
        if len(seed) != 32:
            raise ValueError("signing key file must hold a 32-byte Ed25519 seed")
        self._key = Ed25519PrivateKey.from_private_bytes(seed)
        raw = self.public_key_bytes()
        self.key_id = "ed25519:" + hashlib.sha256(raw).hexdigest()[:16]

    @classmethod
    def initialise(cls, path: Path) -> "LocalFileSigner":
        path = Path(path)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, secrets.token_bytes(32))
        finally:
            os.close(fd)
        return cls(path)

    def public_key_bytes(self) -> bytes:
        return self._key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)

    def sign(self, message: bytes) -> bytes:
        return self._key.sign(message)


def write_checkpoints(conn: psycopg.Connection, signer: LocalFileSigner, witness: Path, *,
                      now: datetime | None = None, every_events: int = 1000, every_seconds: int = 3600) -> list[dict]:
    """Sign every due stream head. Returns the checkpoints written (as witness records)."""
    now = now or datetime.now(UTC)
    heads = conn.execute("SELECT DISTINCT ON (stream_id) stream_id, commit_seq, hash FROM ledger.events "
                         "ORDER BY stream_id, commit_seq DESC").fetchall()
    last = {s: (seq, at) for s, seq, at in conn.execute(
        "SELECT DISTINCT ON (stream_id) stream_id, commit_seq, signed_at FROM ledger.checkpoints "
        "ORDER BY stream_id, commit_seq DESC")}
    conn.commit()
    written = []
    for stream_id, seq, head_hash in heads:
        last_seq, last_at = last.get(stream_id, (0, None))
        new = seq - last_seq
        if new <= 0 or not (new >= every_events or last_at is None or now - last_at >= timedelta(seconds=every_seconds)):
            continue
        head_hash = bytes(head_hash)
        signature = signer.sign(signed_message(stream_id, seq, head_hash, now, signer.key_id))
        record = {"checkpoint_id": str(uuid.uuid7()), "stream_id": str(stream_id), "commit_seq": seq,
                  "head_hash": head_hash.hex(), "signed_at": now.isoformat(), "signing_key_id": signer.key_id,
                  "signature": signature.hex()}
        with conn.transaction():
            conn.execute("""INSERT INTO ledger.checkpoints (checkpoint_id, stream_id, commit_seq, head_hash, signed_at,
                            signing_key_id, signature) VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                         (uuid.UUID(record["checkpoint_id"]), stream_id, seq, head_hash, now, signer.key_id, signature))
            _append_witness(witness, record)
        written.append(record)
    return written


def _append_witness(path: Path, record: dict) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, (json.dumps(record, sort_keys=True) + "\n").encode())
        os.fsync(fd)
    finally:
        os.close(fd)
