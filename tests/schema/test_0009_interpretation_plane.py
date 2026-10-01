"""Tests for migration 0009: the `interp` projection (D-0017): structural only, append-only, scoped, contiguous,
backed by memory events, edges to earlier commits only."""
import uuid

import psycopg
import pytest

from nacre.core.event import ActorKind, EventType, PayloadType, Source
from nacre.ledger.append_event import AppendRequest, Authorship, append_event

MAC = b"\x01" * 32


def _event(s, provider, stream, etype=EventType.MEMORY_EVENT):
    return append_event(s, provider, AppendRequest(
        stream_id=stream, event_type=etype, payload_type=PayloadType.STRUCTURED, actor_kind=ActorKind.SYSTEM,
        actor_id=uuid.UUID(int=1), source=Source.SYSTEM, authorship=Authorship.SCOPE_PRINCIPAL,
        idempotency_key=str(uuid.uuid4()), content={"op": "version"})).envelope


def _version(s, env, obj, version=1, kind="belief", status="active", support="single_source"):
    s.conn.execute("INSERT INTO interp.versions (object_id, version, kind, status, support, stream_id, event_id, "
                   "commit_seq, content_mac) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                   (obj, version, kind, status, support, env.stream_id, env.event_id, env.commit_seq, MAC))


@pytest.fixture
def rw(session, streams):
    p = uuid.uuid4()
    return lambda stream=None: session(p, read=[streams["a"], streams["b"]], write=[stream or streams["a"]])


def test_no_content_column_exists(rw):
    with rw() as s:
        cols = {r[0] for r in s.conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = 'interp'")}
    assert cols <= {"object_id", "version", "kind", "status", "support", "stream_id", "event_id", "commit_seq",
                    "content_mac", "ordinal", "role", "target_event_id", "target_object_id", "target_version",
                    "span_start", "span_end", "span_mac"}


def test_a_version_backed_by_its_memory_event_is_accepted_and_the_head_view_works(rw, provider, streams):
    obj = uuid.uuid4()
    with rw() as s:
        _version(s, _event(s, provider, streams["a"]), obj)
        _version(s, _event(s, provider, streams["a"]), obj, version=2, status="contested")
        assert s.conn.execute("SELECT version, status FROM interp.heads WHERE object_id = %s", (obj,)).fetchone() == (2, "contested")


@pytest.mark.parametrize("bad", ["skip_version", "wrong_event_type", "wrong_seq", "kind_change"])
def test_versions_must_be_contiguous_and_backed(rw, provider, streams, bad):
    obj = uuid.uuid4()
    with pytest.raises(psycopg.errors.RaiseException):
        with rw() as s:
            env = _event(s, provider, streams["a"])
            if bad == "skip_version":
                _version(s, env, obj, version=2)
            elif bad == "wrong_event_type":
                _version(s, _event(s, provider, streams["a"], EventType.STATEMENT), obj)
            elif bad == "wrong_seq":
                s.conn.execute("INSERT INTO interp.versions VALUES (%s,1,'belief','active','quorum',%s,%s,%s,%s)",
                               (obj, env.stream_id, env.event_id, env.commit_seq + 5, MAC))
            else:
                _version(s, env, obj)
                _version(s, _event(s, provider, streams["a"]), obj, version=2, kind="episode", support=None)


@pytest.mark.parametrize("kind,status,support", [("belief", "active", None), ("episode", "active", "quorum"),
                                                 ("fallback", "active", None), ("belief", "fallback", "quorum")])
def test_kind_status_support_rules(rw, provider, streams, kind, status, support):
    with pytest.raises(psycopg.errors.CheckViolation):
        with rw() as s:
            _version(s, _event(s, provider, streams["a"]), uuid.uuid4(), kind=kind, status=status, support=support)


def test_edges_point_only_to_earlier_commits_of_the_same_stream(rw, provider, streams):
    obj = uuid.uuid4()
    with rw() as s:
        support = _event(s, provider, streams["a"])
        _version(s, _event(s, provider, streams["a"]), obj)
        s.conn.execute("INSERT INTO interp.edges (object_id, version, ordinal, stream_id, role, target_event_id, "
                       "span_start, span_end, span_mac) VALUES (%s,1,0,%s,'support',%s,0,10,%s)",
                       (obj, streams["a"], support.event_id, MAC))
    for target in ("later", "other_stream"):
        with pytest.raises(psycopg.errors.RaiseException):
            with rw() as s:
                obj2 = uuid.uuid4()
                _version(s, _event(s, provider, streams["a"]), obj2)
                t = _event(s, provider, streams["a"]) if target == "later" else None
                if target == "other_stream":
                    with rw(streams["b"]) as sb:
                        t = _event(sb, provider, streams["b"])
                s.conn.execute("INSERT INTO interp.edges (object_id, version, ordinal, stream_id, role, target_event_id) "
                               "VALUES (%s,1,0,%s,'support',%s)", (obj2, streams["a"], t.event_id))


def test_append_only_for_the_app_and_even_for_the_owner(rw, provider, streams):
    obj = uuid.uuid4()
    with rw() as s:
        support = _event(s, provider, streams["a"])
        _version(s, _event(s, provider, streams["a"]), obj)
        s.conn.execute("INSERT INTO interp.edges (object_id, version, ordinal, stream_id, role, target_event_id) "
                       "VALUES (%s,1,0,%s,'support',%s)", (obj, streams["a"], support.event_id))
    sqls = ("UPDATE interp.versions SET status = 'superseded' WHERE object_id = %s",
            "DELETE FROM interp.versions WHERE object_id = %s", "DELETE FROM interp.edges WHERE object_id = %s")
    for sql in sqls:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):          # no UPDATE/DELETE grant at all
            with rw() as s:
                s.conn.execute(sql, (obj,))
    for sql in sqls:                                                       # and the trigger stops the table owner
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="append-only"):
            with psycopg.connect(streams["dsn"]["admin"]) as c:
                c.execute(sql, (obj,))


def test_rls_scopes_the_projection(rw, session, provider, streams):
    obj = uuid.uuid4()
    with rw() as s:
        _version(s, _event(s, provider, streams["a"]), obj)
    with session(uuid.uuid4(), read=[streams["b"]], write=[streams["b"]]) as s:
        assert s.conn.execute("SELECT count(*) FROM interp.versions WHERE object_id = %s", (obj,)).fetchone()[0] == 0
        assert s.conn.execute("SELECT count(*) FROM interp.heads").fetchone()[0] == 0
    with rw() as s:
        env = _event(s, provider, streams["a"])                             # a real memory event of stream a
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="row-level security"):
        with session(uuid.uuid4(), read=[streams["a"]], write=[]) as s:   # read-only on a: cannot write its projection
            _version(s, env, uuid.uuid4())


def test_a_version_needs_an_existing_backing_event(rw, streams):
    with pytest.raises(psycopg.errors.RaiseException, match="backed by a memory_event"):
        with rw() as s:
            s.conn.execute("INSERT INTO interp.versions VALUES (%s,1,'belief','active','quorum',%s,%s,1,%s)",
                           (uuid.uuid4(), streams["a"], uuid.uuid4(), MAC))


def test_a_gap_after_the_first_version_is_refused(rw, provider, streams):
    obj = uuid.uuid4()
    with rw() as s:
        _version(s, _event(s, provider, streams["a"]), obj)
    with pytest.raises(psycopg.errors.RaiseException, match="must follow"):
        with rw() as s:
            _version(s, _event(s, provider, streams["a"]), obj, version=3)


def test_rls_is_forced_even_for_the_owner(streams):
    with psycopg.connect(streams["dsn"]["admin"]) as c:
        rows = dict(c.execute("SELECT relname, relforcerowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                              "WHERE n.nspname = 'interp' AND relkind = 'r'").fetchall())
    assert rows == {"versions": True, "edges": True}


@pytest.mark.parametrize("sql", ["ALTER TABLE interp.versions DISABLE TRIGGER versions_check",
                                 "ALTER TABLE interp.versions DISABLE TRIGGER ALL",
                                 "ALTER TABLE interp.edges DISABLE TRIGGER edges_check",
                                 "SET session_replication_role = replica",
                                 "DROP TRIGGER versions_check ON interp.versions"])
def test_the_app_role_cannot_disable_or_bypass_the_projection_triggers(rw, sql):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with rw() as s:
            s.conn.execute(sql)


def test_a_version_cannot_reference_a_memory_event_of_another_stream(rw, session, provider, streams):
    with session(uuid.uuid4(), read=[streams["b"]], write=[streams["b"]]) as sb:
        foreign = _event(sb, provider, streams["b"])
    with pytest.raises(psycopg.errors.RaiseException, match="backed by a memory_event of its stream"):
        with rw() as s:                       # claims stream a while pointing at stream b's event
            s.conn.execute("INSERT INTO interp.versions VALUES (%s,1,'belief','active','quorum',%s,%s,%s,%s)",
                           (uuid.uuid4(), streams["a"], foreign.event_id, foreign.commit_seq, MAC))
