"""Tests for ledger/append_event.py (D-0002, D-0003, D-0004, D-0012): the whole write path against Postgres."""
import random
import string
import threading
import uuid
from dataclasses import fields, replace
from datetime import UTC, datetime

import psycopg
import pytest

from nacre.core.db import DbRole, connect
from nacre.core.event import (ActorKind, Envelope, EventType, PayloadType, Source, TimeBasis, TimePrecision, Trust,
                              TrustBasis)
from nacre.keys.decrypt_payload import Shredded, decrypt_payload
from nacre.ledger.append_event import (AppendError, AppendRequest, Authorship, IdempotencyConflict, OriginalErased,
                                       append_event)
from nacre.ledger.seal_event import GENESIS_PREV_HASH, seal_event
from nacre.scopes.open_scoped_session import open_scoped_session

rng = random.Random(14)


def _gh():  # built at runtime: no secret-shaped literal is committed (D-0010)
    return "gh" + "p_" + "".join(rng.choice(string.ascii_letters + string.digits) for _ in range(36))


def req(stream, **kw):
    base = dict(stream_id=stream, event_type=EventType.MESSAGE, payload_type=PayloadType.TEXT,
                actor_kind=ActorKind.AGENT, actor_id=uuid.UUID(int=7), source=Source.CHAT,
                idempotency_key=str(uuid.uuid4()), content="hello")
    base.update(kw)
    return AppendRequest(**base)


@pytest.fixture
def writer(session, streams):
    p = uuid.uuid4()
    return lambda: session(p, read=[streams["a"]], write=[streams["a"]])


def _rows(dsn, stream):
    cols = [f.name for f in fields(Envelope)]
    with psycopg.connect(dsn) as c:
        return [dict(zip(cols, r)) for r in c.execute(
            f"SELECT {', '.join(cols)} FROM ledger.events WHERE stream_id = %s ORDER BY commit_seq", (stream,))]


def _read_body(session, provider, stream, env):
    aad = {k: getattr(env, k) for k in ("envelope_version", "event_id", "stream_id", "key_id", "event_type", "payload_type")}
    with session(uuid.uuid4(), read=[stream]) as s:
        return decrypt_payload(s.conn, provider, aad, env.body_ciphertext)


# ---- the write path -----------------------------------------------------------------------------------
def test_append_seals_encrypts_and_reads_back(writer, provider, streams, session):
    a = streams["a"]
    with writer() as s:
        r1 = append_event(s, provider, req(a, content="first"))
        r2 = append_event(s, provider, req(a, content="second"))
    assert (r1.created, r1.envelope.commit_seq, r1.envelope.prev_hash) == (True, 1, GENESIS_PREV_HASH)
    assert (r2.envelope.commit_seq, r2.envelope.prev_hash) == (2, r1.envelope.hash)
    for row in _rows(streams["dsn"]["admin"], a):
        fields_ = {k: (bytes(v) if isinstance(v, memoryview) else v) for k, v in row.items() if k != "hash"}
        assert seal_event(fields_) == bytes(row["hash"])
    body = _read_body(session, provider, a, r2.envelope)
    assert body == {"content_version": 1, "content": "second"}
    assert r1.envelope.envelope_version == 2 and r1.envelope.trust_basis == TrustBasis.ASSERTED


def test_secrets_are_stripped_before_encryption(writer, provider, streams, session):
    a, token = streams["a"], _gh()
    with writer() as s:
        r = append_event(s, provider, req(a, content={"note": f"use {token} for CI", "n": 3},
                                          source_ref=f"https://ci.example.com/run?token={token}"))
    assert r.redactions and "github-pat" in r.redactions
    body = _read_body(session, provider, a, r.envelope)
    assert token not in str(body) and body["content"]["n"] == 3 and "github-pat" in body["redactions"]
    with psycopg.connect(streams["dsn"]["admin"]) as c:
        for row in c.execute("SELECT * FROM ledger.events"):
            assert token.encode() not in b"".join(bytes(v) if isinstance(v, (bytes, memoryview)) else str(v).encode()
                                                  for v in row)


def test_public_credentials_are_kept_and_tagged(writer, provider, streams, session):
    dsn = "https://" + "b" * 32 + "@o1.ingest.sentry.io/42"
    with writer() as s:
        r = append_event(s, provider, req(streams["a"], content=f"SENTRY_DSN={dsn}"))
    body = _read_body(session, provider, streams["a"], r.envelope)
    assert dsn in body["content"] and body["public_credentials"] == ["sentry-dsn-public"]


# ---- trust (D-0012 part A) ----------------------------------------------------------------------------
@pytest.mark.parametrize("source,authorship,payload,expected", [
    (Source.CHAT, Authorship.EXTERNAL, PayloadType.TEXT, Trust.UNTRUSTED),                 # default
    (Source.CHAT, Authorship.SCOPE_PRINCIPAL, PayloadType.TEXT, Trust.TRUSTED),
    (Source.GIT, Authorship.SCOPE_PRINCIPAL, PayloadType.TEXT, Trust.TRUSTED),             # member's commit message
    (Source.GIT, Authorship.EXTERNAL, PayloadType.TEXT, Trust.UNTRUSTED),                  # non-member's commit message
    (Source.CI, Authorship.INTEGRATION_RESULT, PayloadType.STRUCTURED, Trust.TRUSTED),     # CI status fields
    (Source.CI, Authorship.SCOPE_PRINCIPAL, PayloadType.TEXT, Trust.UNTRUSTED),            # CI log text
    (Source.WEB, Authorship.SCOPE_PRINCIPAL, PayloadType.TEXT, Trust.UNTRUSTED),
    (Source.TOOL, Authorship.SCOPE_PRINCIPAL, PayloadType.TEXT, Trust.UNTRUSTED),
    (Source.TOOL, Authorship.EXTERNAL, PayloadType.STRUCTURED, Trust.UNTRUSTED),
    # web/tool are untrusted before any authorship claim is even considered:
    (Source.TOOL, Authorship.INTEGRATION_RESULT, PayloadType.STRUCTURED, Trust.UNTRUSTED),
    (Source.WEB, Authorship.INTEGRATION_RESULT, PayloadType.STRUCTURED, Trust.UNTRUSTED),
])
def test_trust_by_source_and_author(writer, provider, streams, source, authorship, payload, expected):
    content = {"status": "passed"} if payload == PayloadType.STRUCTURED else "text"
    with writer() as s:
        r = append_event(s, provider, req(streams["a"], source=source, authorship=authorship, payload_type=payload,
                                          content=content))
    assert r.envelope.trust == expected


def test_integration_result_claim_on_log_text_is_rejected(writer, provider, streams):
    with writer() as s, pytest.raises(AppendError, match="integration_result"):
        append_event(s, provider, req(streams["a"], source=Source.CI, authorship=Authorship.INTEGRATION_RESULT))


def test_observed_time_needs_trust(writer, provider, streams):
    at = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
    with writer() as s, pytest.raises(AppendError, match="observed"):
        append_event(s, provider, req(streams["a"], occurred_at=at, occurred_at_basis=TimeBasis.OBSERVED,
                                      occurred_at_precision=TimePrecision.SECOND))


# ---- idempotency (D-0012 part B; gate item 1) -------------------------------------------------------------
def test_exact_retry_returns_the_original_and_writes_nothing(writer, provider, streams):
    a = streams["a"]
    request = req(a, content="once")
    with writer() as s:
        first = append_event(s, provider, request)
    with writer() as s:
        again = append_event(s, provider, request)
    assert again.created is False and again.envelope == first.envelope
    assert len(_rows(streams["dsn"]["admin"], a)) == 1


def test_same_key_different_request_conflicts(writer, provider, streams):
    request = req(streams["a"], content="one")
    with writer() as s:
        append_event(s, provider, request)
    with writer() as s, pytest.raises(IdempotencyConflict):
        append_event(s, provider, replace(request, content="two"))


def test_same_key_different_principal_conflicts(session, provider, streams):
    a = streams["a"]
    request = req(a)
    with session(uuid.uuid4(), read=[a], write=[a]) as s:
        append_event(s, provider, request)
    with session(uuid.uuid4(), read=[a], write=[a]) as s, pytest.raises(IdempotencyConflict):
        append_event(s, provider, request)


def test_retry_of_an_erased_original_is_rejected_distinctly(writer, provider, streams):
    request = req(streams["a"])
    with writer() as s:
        first = append_event(s, provider, request)
    with psycopg.connect(streams["dsn"]["admin"]) as c:
        c.execute("DELETE FROM keys.data_keys WHERE key_id = %s", (first.envelope.key_id,))
    with writer() as s, pytest.raises(OriginalErased):
        append_event(s, provider, request)


@pytest.mark.parametrize("key", ["retry-1", "0" * 64, str(uuid.uuid1()), str(uuid.uuid4()).upper()],
                         ids=["label", "content-hash", "uuid1", "non-canonical"])
def test_idempotency_key_must_be_a_canonical_random_uuid(writer, provider, streams, key):
    with writer() as s, pytest.raises(AppendError, match="idempotency_key"):
        append_event(s, provider, req(streams["a"], idempotency_key=key))


# ---- validation --------------------------------------------------------------------------------------------
@pytest.mark.parametrize("kw,match", [
    (dict(occurred_at=datetime(2026, 9, 30, 12, 0, 5, tzinfo=UTC), occurred_at_basis=TimeBasis.ASSERTED,
          occurred_at_precision=TimePrecision.MINUTE), "finer than"),
    (dict(occurred_at=datetime(2026, 9, 30, tzinfo=UTC)), "go together"),
    (dict(actor_model="alice@example.com"), "actor_model"),
    (dict(event_type=EventType.CORRECTION), "caused_by"),
    (dict(caused_by=uuid.uuid4(), event_type=EventType.CORRECTION), "same stream"),
    (dict(content={"score": 0.5}), "subset"),
    (dict(person={"name": "Ada", "age": "36"}), "person"),
    (dict(org_id="acme-corp"), "UUID"),
], ids=["precision", "time-parts", "short-id", "correction", "caused-by-foreign", "float", "person", "natural-id"])
def test_invalid_requests_are_rejected(writer, provider, streams, kw, match):
    with writer() as s, pytest.raises(AppendError, match=match):
        append_event(s, provider, req(streams["a"], **kw))


def test_nothing_is_written_when_a_request_is_rejected(writer, provider, streams):
    with pytest.raises(AppendError):
        with writer() as s:
            append_event(s, provider, req(streams["a"], actor_model="bad model"))
    assert _rows(streams["dsn"]["admin"], streams["a"]) == []


# ---- keys: subject and month (D-0004) --------------------------------------------------------------------
def test_person_statements_use_the_person_subject_others_the_stream(writer, provider, streams):
    a, person = streams["a"], uuid.uuid4()
    with writer() as s:
        said = append_event(s, provider, req(a, event_type=EventType.STATEMENT, actor_kind=ActorKind.PERSON,
                                             actor_id=person, authorship=Authorship.SCOPE_PRINCIPAL))
        ran = append_event(s, provider, req(a, event_type=EventType.ACTION))
    with psycopg.connect(streams["dsn"]["admin"]) as c:
        subject = dict(c.execute("SELECT key_id, subject_id FROM keys.data_keys"))
    assert subject[said.envelope.key_id] == person and subject[ran.envelope.key_id] == a


def test_erasing_the_person_shreds_only_their_events(writer, provider, streams, session):
    a, person = streams["a"], uuid.uuid4()
    with writer() as s:
        said = append_event(s, provider, req(a, event_type=EventType.MESSAGE, actor_kind=ActorKind.PERSON,
                                             actor_id=person, content="my words"))
        other = append_event(s, provider, req(a, content="system words"))
    with psycopg.connect(streams["dsn"]["admin"]) as c:
        c.execute("DELETE FROM keys.data_keys WHERE subject_id = %s", (person,))
    assert _read_body(session, provider, a, said.envelope) == Shredded(said.envelope.key_id)
    assert _read_body(session, provider, a, other.envelope)["content"] == "system words"


# ---- isolation and concurrency ---------------------------------------------------------------------------
def test_read_only_principal_cannot_append(session, provider, streams):
    with session(uuid.uuid4(), read=[streams["a"]]) as s, pytest.raises(psycopg.errors.InsufficientPrivilege):
        append_event(s, provider, req(streams["a"]))


def test_concurrent_writers_produce_a_gapless_valid_chain(session, provider, streams):
    a, principal = streams["a"], uuid.uuid4()
    with session(principal, read=[a], write=[a]):
        pass                                              # records the grant once
    errors, per_thread = [], 10

    def worker():
        try:
            with connect(DbRole.APP, dsn=streams["dsn"]["app"]) as conn:
                for _ in range(per_thread):
                    with open_scoped_session(conn, principal) as s:
                        append_event(s, provider, req(a, content="x"))
        except Exception as exc:  # noqa: BLE001 - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    rows = _rows(streams["dsn"]["admin"], a)
    assert [r["commit_seq"] for r in rows] == list(range(1, 16 * per_thread + 1))
    prev = GENESIS_PREV_HASH
    for row in rows:
        assert bytes(row["prev_hash"]) == prev
        prev = bytes(row["hash"])
