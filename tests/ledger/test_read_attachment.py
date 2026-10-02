"""Tests for store_attachment + read_attachment + append_event attachments (D-0013, owner additions)."""
import hashlib
import io
import random
import string
import uuid

import psycopg
import pytest
from PIL import Image

from nacre.core.event import ActorKind, EventType, PayloadType, Source
from nacre.keys.decrypt_payload import Shredded
from nacre.ledger.append_event import AppendError, AppendRequest, append_event
from nacre.ledger.local_disk_blob_store import LocalDiskBlobStore
from nacre.ledger.read_attachment import AttachmentReadError, read_attachment
from nacre.ledger.read_stream import read_stream
from nacre.ledger.store_attachment import MAX_ATTACHMENT_BYTES, AttachmentError, store_attachment

rng = random.Random(15)

def _png():
    """A real, clean PNG: binaries must pass the D-0027 scan, so a fake PNG header would now be unscannable."""
    out = io.BytesIO()
    Image.new("RGB", (64, 64), (200, 220, 240)).save(out, "PNG")
    return out.getvalue()


PNG = _png()


def _gh():
    return "gh" + "p_" + "".join(rng.choice(string.ascii_letters + string.digits) for _ in range(36))


def req(stream, **kw):
    base = dict(stream_id=stream, event_type=EventType.RESULT, payload_type=PayloadType.IMAGE, actor_kind=ActorKind.TOOL,
                actor_id=uuid.UUID(int=9), source=Source.TOOL, idempotency_key=str(uuid.uuid4()), content=None,
                attachment=PNG, attachment_media_type="image/png", attachment_description="screenshot of the failing page")
    base.update(kw)
    return AppendRequest(**base)


@pytest.fixture
def blobs(tmp_path):
    return LocalDiskBlobStore(tmp_path / "blobs")


@pytest.fixture
def rw(session, streams):
    p = uuid.uuid4()
    return lambda: session(p, read=[streams["a"]], write=[streams["a"]])


def test_round_trip_and_body_metadata(rw, provider, streams, blobs):
    with rw() as s:
        env = append_event(s, provider, req(streams["a"]), blob_store=blobs).envelope
    with rw() as s:
        assert read_attachment(s, provider, blobs, env) == PNG
        (e,) = read_stream(s, provider, streams["a"])
    extractors = e.body["attachment"].pop("extractors")                    # D-0008 amendment 7
    assert e.body["attachment"] == {"media_type": "image/png", "description": "screenshot of the failing page",
                                    "scan": "binary-scanned"}
    assert extractors["Pillow"] == "12.3.0" and extractors["rapidocr"] == "3.9.2"
    assert env.attachment_sha256 == hashlib.sha256(blobs.get(env.attachment_ref)).digest()
    assert PNG not in blobs.get(env.attachment_ref)


def test_same_bytes_under_same_key_dedup_other_key_does_not(rw, provider, streams, blobs, session):
    with rw() as s:
        e1 = append_event(s, provider, req(streams["a"]), blob_store=blobs).envelope
        e2 = append_event(s, provider, req(streams["a"]), blob_store=blobs).envelope
    with session(uuid.uuid4(), read=[streams["b"]], write=[streams["b"]]) as s:
        e3 = append_event(s, provider, req(streams["b"]), blob_store=blobs).envelope
    assert e1.attachment_ref == e2.attachment_ref and e1.attachment_sha256 == e2.attachment_sha256
    assert e3.attachment_ref != e1.attachment_ref                       # other stream, other key: no cross-scope dedup


def test_file_is_written_before_the_event_commits(rw, provider, streams, blobs):
    # Owner addition: an event must never point to a missing file. A failed append may leave a harmless orphan.
    with pytest.raises(RuntimeError):
        with rw() as s:
            env = append_event(s, provider, req(streams["a"]), blob_store=blobs).envelope
            assert blobs.exists(env.attachment_ref)                     # already on disk inside the transaction
            raise RuntimeError("abort after writing")
    with psycopg.connect(streams["dsn"]["admin"]) as c:
        assert c.execute("SELECT count(*) FROM ledger.events").fetchone()[0] == 0
    assert blobs.exists(env.attachment_ref)                             # orphan remains, harmlessly


def test_text_attachments_are_secret_stripped(rw, provider, streams, blobs):
    token = _gh()
    log = f"step 3: export TOKEN={token}\nok\n".encode()
    with rw() as s:
        r = append_event(s, provider, req(streams["a"], payload_type=PayloadType.TEXT, attachment=log,
                                          attachment_media_type="text/plain"), blob_store=blobs)
    with rw() as s:
        stored = read_attachment(s, provider, blobs, r.envelope)
    assert token.encode() not in stored and b"[REDACTED:" in stored and "github-pat" in r.redactions


@pytest.mark.parametrize("damage", ["flip", "truncate", "swap"])
def test_every_read_verifies_the_fingerprints(rw, provider, streams, blobs, damage):
    with rw() as s:
        env = append_event(s, provider, req(streams["a"]), blob_store=blobs).envelope
        other = append_event(s, provider, req(streams["a"], attachment=b"other bytes"), blob_store=blobs).envelope
    path = next(p for p in blobs._root.rglob(env.attachment_ref.hex()))
    if damage == "flip":
        b = bytearray(path.read_bytes()); b[-1] ^= 1; path.write_bytes(bytes(b))
    elif damage == "truncate":
        path.write_bytes(path.read_bytes()[:40])
    else:
        path.write_bytes(blobs.get(other.attachment_ref))               # a valid blob, but the wrong one
    with rw() as s, pytest.raises(AttachmentReadError, match="tampered|does not match"):
        read_attachment(s, provider, blobs, env)


def test_missing_blob_is_an_error_not_empty(rw, provider, streams, blobs):
    with rw() as s:
        env = append_event(s, provider, req(streams["a"]), blob_store=blobs).envelope
    next(p for p in blobs._root.rglob(env.attachment_ref.hex())).unlink()
    with rw() as s, pytest.raises(AttachmentReadError, match="missing"):
        read_attachment(s, provider, blobs, env)


def test_shredded_key_reads_as_shredded(rw, provider, streams, blobs):
    with rw() as s:
        env = append_event(s, provider, req(streams["a"]), blob_store=blobs).envelope
    with psycopg.connect(streams["dsn"]["admin"]) as c:
        c.execute("DELETE FROM keys.stream_master_keys WHERE stream_id = %s", (streams["a"],))
    with rw() as s:
        assert read_attachment(s, provider, blobs, env) == Shredded(env.key_id)


def test_limits_and_validation(rw, provider, streams, blobs):
    with rw() as s:
        with pytest.raises(AppendError, match="blob store"):
            append_event(s, provider, req(streams["a"]))
        with pytest.raises(AppendError, match="media type"):
            append_event(s, provider, req(streams["a"], attachment_media_type="PNG"), blob_store=blobs)
        with pytest.raises(AppendError, match="without an attachment"):
            append_event(s, provider, req(streams["a"], attachment=None), blob_store=blobs)
        from nacre.keys.get_or_create_key import get_or_create_key
        from datetime import date
        key = get_or_create_key(s.conn, provider, streams["a"], streams["a"], date(2026, 9, 1))
        with pytest.raises(AttachmentError, match="limit"):
            store_attachment(s.conn, key, blobs, b"x" * (MAX_ATTACHMENT_BYTES + 1))


def test_plaintext_must_match_its_ref_even_if_the_blob_authenticates(rw, provider, streams, blobs):
    # Guards against a writer-side bug: a blob correctly sealed under a ref, but over the WRONG plaintext.
    from nacre.keys.encrypt_payload import seal_bytes
    from nacre.keys.get_or_create_key import load_key
    from nacre.ledger.store_attachment import AAD_PREFIX
    with rw() as s:
        env = append_event(s, provider, req(streams["a"]), blob_store=blobs).envelope
    with rw() as s:
        key = load_key(s.conn, provider, env.key_id)
        buggy = seal_bytes(s.conn, key, AAD_PREFIX + env.attachment_ref, b"not the attached bytes")
    path = next(p for p in blobs._root.rglob(env.attachment_ref.hex()))
    path.write_bytes(buggy)
    from dataclasses import replace
    forged = replace(env, attachment_sha256=hashlib.sha256(buggy).digest())
    with rw() as s, pytest.raises(AttachmentReadError, match="attachment_ref"):
        read_attachment(s, provider, blobs, forged)


@pytest.mark.parametrize("declared", ["image/png", "application/octet-stream", "text/plain"])
def test_text_is_detected_by_content_not_by_declared_media_type(rw, provider, streams, blobs, declared):
    # Owner fix: relabelling text as binary must not bypass stripping.
    token = _gh()
    with rw() as s:
        r = append_event(s, provider, req(streams["a"], payload_type=PayloadType.TEXT, attachment=f"key={token}\n".encode(),
                                          attachment_media_type=declared), blob_store=blobs)
        (e,) = [x for x in read_stream(s, provider, streams["a"]) if x.envelope.event_id == r.envelope.event_id]
        stored = read_attachment(s, provider, blobs, r.envelope)
    assert token.encode() not in stored and e.body["attachment"]["scan"] == "text-scanned"


def test_binary_labelled_as_text_is_scanned_as_binary(rw, provider, streams, blobs):
    # D-0027 test 5: a binary declared as text/plain is still decided by content (scanned as binary, not stripped).
    with rw() as s:
        r = append_event(s, provider, req(streams["a"], attachment_media_type="text/plain"), blob_store=blobs)
        (e,) = read_stream(s, provider, streams["a"])
        assert read_attachment(s, provider, blobs, r.envelope) == PNG
    assert e.body["attachment"]["scan"] == "binary-scanned"
