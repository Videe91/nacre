"""Tests for interface/mcp_server.py and interface/run_operation.py (R22): D-0026 §4 and gate items 9-10. Every call
is authenticated (revocation is immediate); over-claims are rejected and write nothing; valid claims are written
`verified`; the surface has no admin or erasure tool; HTTP refuses a non-loopback bind; the 1 MiB and rate limits
apply; errors carry a stable code and never the token."""
import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from nacre.capture.record_decision import record_decision
from nacre.capture.record_outcome import Section, record_outcome
from nacre.core.db import DbRole, connect, open_pool
from nacre.core.event import ActorKind, Source
from nacre.interface.authenticate_principal import authenticate_principal
from nacre.interface.limit_rate import RateLimiter
from nacre.interface.mcp_server import TOOLS, BindRefused, build_server, check_bind, serve_http
from nacre.interface.run_operation import Caller, OperationError, Services, run_operation
from nacre.interface.token_format import new_token, token_mac
from nacre.ledger.append_event import Authorship
from nacre.recall.embed_local import DIM
from nacre.recall.index_version import default_embedder
from nacre.recall.load_index_cache import IndexCache
from nacre.scopes.open_scoped_session import open_scoped_session
from nacre.stores.promote_if_supported import promote_if_supported
from nacre.stores.propose_lesson import propose_lesson

KEY = os.urandom(32)
NOW = datetime.now(UTC)


@pytest.fixture
def world(migrated_db, streams, provider):
    admin = psycopg.connect(migrated_db["principal_admin"], autocommit=True)
    auth = psycopg.connect(migrated_db["auth"])
    pool = open_pool(DbRole.APP, dsn=migrated_db["app"], max_size=2)
    services = Services(pool, provider, IndexCache(provider, dim=DIM), default_embedder(), 6000, "test-1")

    def principal(kind="agent", sources=()):
        pid = uuid.uuid4()
        admin.execute("INSERT INTO auth.principals (principal_id, org_id, kind, display_name, service_sources, "
                      "config_event_id) VALUES (%s, %s, %s, 'p', %s, %s)",
                      (pid, streams["org"], kind, list(sources), uuid.uuid4()))
        token, tid, secret = new_token()
        admin.execute("INSERT INTO auth.tokens (token_id, principal_id, token_mac, expires_at) VALUES (%s, %s, %s, %s)",
                      (tid, pid, token_mac(KEY, secret), NOW + timedelta(days=1)))
        with psycopg.connect(migrated_db["admin"]) as a:
            a.execute("INSERT INTO scopes.scope_grants (principal_id, stream_id, org_id, can_read, can_append, "
                      "source_event_id, source_seq) VALUES (%s, %s, %s, true, true, %s, %s)",
                      (pid, streams["a"], streams["org"], uuid.uuid4(), 5_000_000 + uuid.uuid4().int % 10**6))
        return pid, tid, token

    holder = {"token": None}

    def server(limiter=None):
        return build_server(services, lambda t: authenticate_principal(auth, KEY, t), lambda ctx: holder["token"],
                            limiter)

    yield {"principal": principal, "server": server, "holder": holder, "admin": admin, "services": services,
           "a": streams["a"], "dsn": migrated_db}
    pool.close()
    auth.close()
    admin.close()


def call(server, name, args):
    return asyncio.run(server.call_tool(name, args))


def payload(result):
    assert not result.is_error
    return result.structured_content


def _decision(a, **kw):
    return {"stream_id": str(a), "idempotency_key": str(uuid.uuid4()), "decision_text": "retry the flaky job", **kw}


def _count(dsn):
    with psycopg.connect(dsn["admin"]) as c:
        return c.execute("SELECT count(*) FROM ledger.events").fetchone()[0]


def test_the_surface_is_exactly_the_decided_tools_with_no_admin_or_erasure_tool(world):
    names = sorted(t.name for t in asyncio.run(world["server"]().list_tools()))
    assert names == sorted(TOOLS)
    banned = ("erase", "shred", "token", "principal", "key", "scope", "grant", "delegat", "revoke", "admin", "delete")
    assert not [n for n in names if any(b in n for b in banned)]


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.5", "10.0.0.1", "::", "example.com", ""])
def test_a_non_loopback_http_bind_refuses_to_start(host, world):
    with pytest.raises(BindRefused):
        check_bind(host)
    with pytest.raises(BindRefused):
        serve_http(world["services"], lambda t: None, host=host)


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost", "127.0.0.2"])
def test_loopback_binds_are_allowed(host):
    check_bind(host)


def test_a_verified_agent_write_and_immediate_revocation(world):
    pid, tid, token = world["principal"]()
    srv = world["server"]()
    world["holder"]["token"] = token
    out = payload(call(srv, "record_decision", _decision(world["a"])))
    assert out["trust_basis"] == "verified" and out["created"] is True
    world["admin"].execute("UPDATE auth.tokens SET revoked_at = now() WHERE token_id = %s", (tid,))
    with pytest.raises(ToolError, match=": unauthenticated: revoked") as e:
        call(srv, "record_decision", _decision(world["a"]))
    assert token not in str(e.value)


def test_missing_or_malformed_tokens_are_unauthenticated_and_never_echoed(world):
    srv = world["server"]()
    with pytest.raises(ToolError, match=": unauthenticated: no token"):
        call(srv, "record_decision", _decision(world["a"]))
    bad = "nacre_pat_" + "x" * 30
    world["holder"]["token"] = bad
    with pytest.raises(ToolError, match=": unauthenticated: malformed") as e:
        call(srv, "record_decision", _decision(world["a"]))
    assert bad not in str(e.value)


def test_over_claims_are_rejected_and_write_nothing(world):
    pid, _, token = world["principal"]()
    srv = world["server"]()
    world["holder"]["token"] = token
    before = _count(world["dsn"])
    for args in (_decision(world["a"], source="review"), _decision(world["a"], actor_kind="person"),
                 _decision(uuid.uuid4())):
        with pytest.raises(ToolError, match=": (forbidden_claim|scope_not_granted)"):
            call(srv, "record_decision", args)
    with pytest.raises(ToolError, match=": forbidden_claim"):          # an agent cannot plant a correction
        call(srv, "record_correction", {"stream_id": str(world["a"]), "idempotency_key": str(uuid.uuid4()),
                                        "correction_of": str(uuid.uuid4()), "text": "x", "source": "chat",
                                        "authorship": "external", "actor_kind": "agent"})
    assert _count(world["dsn"]) == before


def test_arguments_over_one_mebibyte_are_refused_before_authentication(world):
    srv = world["server"]()
    world["holder"]["token"] = None
    with pytest.raises(ToolError, match=": invalid_request: arguments exceed 1 MiB"):
        call(srv, "record_decision", _decision(world["a"], decision_text="x" * (1024 * 1024)))


def test_the_rate_limit_refuses_with_a_retry_after(world):
    _, _, token = world["principal"]()
    srv = world["server"](RateLimiter(60, 2))
    world["holder"]["token"] = token
    call(srv, "record_decision", _decision(world["a"]))
    call(srv, "record_decision", _decision(world["a"]))
    with pytest.raises(ToolError, match=r": rate_limited: retry after \d"):
        call(srv, "record_decision", _decision(world["a"]))


def test_recall_through_mcp_returns_the_frame_id_and_memory_and_commits_a_trace(world, provider):
    pid, _, token = world["principal"]()
    a = world["a"]
    text = "Pin the payments base image by digest before release."
    with connect(DbRole.APP, dsn=world["dsn"]["app"]) as conn, open_scoped_session(conn, pid) as s:
        d = record_decision(s, provider, stream_id=a, actor_kind=ActorKind.AGENT, actor_id=pid, source=Source.CHAT,
                            authorship=Authorship.SCOPE_PRINCIPAL, idempotency_key=str(uuid.uuid4()),
                            decision_text="first attempt").envelope
        o = record_outcome(s, provider, stream_id=a, idempotency_key=str(uuid.uuid4()), outcome_for=d.event_id,
                           success=False, source=Source.REVIEW, actor_kind=ActorKind.SYSTEM, actor_id=uuid.uuid4(),
                           authorship=Authorship.INTEGRATION_RESULT,
                           sections=(Section("status", "FAIL"), Section("correction", text))).envelope
        prop = propose_lesson(s, provider, stream_id=a, decision_id=d.event_id, outcome_id=o.event_id,
                              section_index=1, span=(0, len(text)), nucleus="Pin the payments base image").event_id
        assert promote_if_supported(s, provider, a, prop).status == "active"
    world["holder"]["token"] = token
    before = _count(world["dsn"])
    out = payload(call(world["server"](), "recall_context",
                       {"issuing_stream": str(a), "scopes": [["project", str(a)]],
                        "query": "Should the payments release pin the base image?"}))
    assert len(out["frame_id"]) == 64 and "Pin the payments base image" in out["memory"]
    assert _count(world["dsn"]) == before + 1                        # exactly the trace


def test_in_process_callers_write_asserted_and_failures_have_stable_codes(world):
    pid, _, _ = world["principal"]()
    svc = world["services"]
    out = run_operation(svc, Caller(pid), "record_decision", _decision(world["a"]))
    assert out["trust_basis"] == "asserted"
    for op, args, code in (("drop_everything", {}, "unknown_operation"),
                           ("record_decision", {"stream_id": "not-a-uuid"}, "invalid_request"),
                           ("record_decision", _decision(world["a"], stakes=["no-such-tag"]), "invalid_request")):
        with pytest.raises(OperationError) as e:
            run_operation(svc, Caller(pid), op, args)
        assert e.value.code == code


def _contents(world, provider, pid):
    from nacre.ledger.read_stream import read_stream
    with connect(DbRole.APP, dsn=world["dsn"]["app"]) as conn, open_scoped_session(conn, pid) as s:
        return {str(e.envelope.event_id): e.body["content"] for e in read_stream(s, provider, world["a"])}


def test_record_tools_accept_addresses_and_pass_them_through(world, provider):
    """D-0025 §3 (the D-0018 amendment): every record_* tool takes `addresses`; they land sorted in the event body,
    and an invalid one is invalid_request with nothing written."""
    tools = {t.name: t for t in asyncio.run(world["server"]().list_tools())}
    assert all("addresses" in tools[n].input_schema["properties"] for n in TOOLS if n.startswith("record_"))
    pid, _, token = world["principal"]()
    srv = world["server"]()
    world["holder"]["token"] = token
    addr = ["system:payments", "code:src/payments/retry.py"]
    d = payload(call(srv, "record_decision", _decision(world["a"], addresses=addr)))["event_id"]
    k = lambda: str(uuid.uuid4())                                   # noqa: E731
    p = payload(call(srv, "record_prediction", {"stream_id": str(world["a"]), "idempotency_key": k(), "decision_id": d,
                                               "expected_outcome": "green", "expected_success": True,
                                               "addresses": ["entity:VX-41"]}))["event_id"]
    ac = payload(call(srv, "record_action", {"stream_id": str(world["a"]), "idempotency_key": k(), "decision_id": d,
                                            "action_kind": "edit", "description": "patch", "addresses": ["file:a.py"]}))
    o = payload(call(srv, "record_outcome", {"stream_id": str(world["a"]), "idempotency_key": k(), "outcome_for": d,
                                            "success": None, "sections": [{"role": "status", "text": "ran"}],
                                            "addresses": ["domain:billing", "cluster:eu"]}))
    got = _contents(world, provider, pid)
    assert got[d]["addresses"] == sorted(addr) and got[p]["addresses"] == ["entity:VX-41"]
    assert got[ac["event_id"]]["addresses"] == ["file:a.py"]
    assert got[o["event_id"]]["addresses"] == ["cluster:eu", "domain:billing"]
    before = _count(world["dsn"])
    with pytest.raises(ToolError, match=": invalid_request: "):
        call(srv, "record_decision", _decision(world["a"], addresses=["payments"]))
    assert _count(world["dsn"]) == before


def test_a_prediction_without_an_expected_success_is_recorded(world):
    _, _, token = world["principal"]()
    world["holder"]["token"] = token
    srv = world["server"]()
    d = payload(call(srv, "record_decision", _decision(world["a"])))["event_id"]
    p = payload(call(srv, "record_prediction", {"stream_id": str(world["a"]), "idempotency_key": str(uuid.uuid4()),
                                                "decision_id": d, "expected_outcome": "the retry succeeds"}))
    assert p["created"] is True and p["trust_basis"] == "verified"
