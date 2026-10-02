"""Tests for sdk/client.py (R23, D-0026 §5): the same operations over real Streamable HTTP (bearer token) and a real
stdio server process (NACRE_TOKEN), plus the in-process mode (writes `asserted`); typed errors; a token is never sent
over plain HTTP to a non-loopback host and never appears in an error or repr."""
import os
import socket
import sys
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest
import uvicorn

from nacre.core.db import DbRole, open_pool
from nacre.interface.authenticate_principal import authenticate_principal
from nacre.interface.mcp_server import _bearer, build_server
from nacre.interface.run_operation import Services
from nacre.interface.token_format import new_token, token_mac
from nacre.recall.embed_local import DIM
from nacre.recall.index_version import default_embedder
from nacre.recall.load_index_cache import IndexCache
from nacre.sdk.client import Client, NacreError

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime.now(UTC)


@pytest.fixture
def world(migrated_db, streams, provider, tmp_path):
    key = os.urandom(32)
    key_file = tmp_path / "token.key"
    key_file.write_bytes(key)
    key_file.chmod(0o600)
    admin = psycopg.connect(migrated_db["principal_admin"], autocommit=True)
    pid = uuid.uuid4()
    admin.execute("INSERT INTO auth.principals (principal_id, org_id, kind, display_name, config_event_id) "
                  "VALUES (%s, %s, 'agent', 'p', %s)", (pid, streams["org"], uuid.uuid4()))
    token, tid, secret = new_token()
    admin.execute("INSERT INTO auth.tokens (token_id, principal_id, token_mac, expires_at) VALUES (%s, %s, %s, %s)",
                  (tid, pid, token_mac(key, secret), NOW + timedelta(days=1)))
    with psycopg.connect(migrated_db["admin"]) as a:
        a.execute("INSERT INTO scopes.scope_grants (principal_id, stream_id, org_id, can_read, can_append, "
                  "source_event_id, source_seq) VALUES (%s, %s, %s, true, true, %s, 7000001)",
                  (pid, streams["a"], streams["org"], uuid.uuid4()))
    pool = open_pool(DbRole.APP, dsn=migrated_db["app"], max_size=2)
    services = Services(pool, provider, IndexCache(provider, dim=DIM), default_embedder(), 6000, "test-1")
    yield {"pid": pid, "tid": tid, "token": token, "key": key, "key_file": key_file, "services": services,
           "a": streams["a"], "dsn": migrated_db, "admin": admin, "root": tmp_path / "rootkeys"}
    pool.close()
    admin.close()


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def http_url(world):
    auth_pool = open_pool(DbRole.AUTH, dsn=world["dsn"]["auth"], max_size=2)

    def authenticate(token):
        with auth_pool.connection() as c:
            return authenticate_principal(c, world["key"], token)
    app = build_server(world["services"], authenticate, _bearer).streamable_http_app()
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", lifespan="on"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/mcp"
    server.should_exit = True
    t.join(10)
    auth_pool.close()


def _decide(client, a, **kw):
    return client.record_decision(a, str(uuid.uuid4()), "retry the flaky job", **kw)


def test_http_writes_verified_with_the_bearer_token_and_errors_are_typed(world, http_url):
    c = Client(http_url, world["token"])
    assert _decide(c, world["a"])["trust_basis"] == "verified"
    with pytest.raises(NacreError) as e:
        _decide(c, world["a"], source="review")
    assert e.value.code == "forbidden_claim"
    with pytest.raises(NacreError) as e:
        _decide(Client(http_url, "nacre_pat_" + "y" * 40), world["a"])
    assert e.value.code == "unauthenticated" and "y" * 40 not in str(e.value)
    world["admin"].execute("UPDATE auth.tokens SET revoked_at = now() WHERE token_id = %s", (world["tid"],))
    with pytest.raises(NacreError) as e:
        _decide(c, world["a"])
    assert (e.value.code, e.value.detail) == ("unauthenticated", "revoked")
    assert world["token"] not in repr(c) and world["token"] not in str(e.value)


def test_stdio_launches_the_server_with_the_token_in_its_env_only(world):
    d = world["dsn"]
    cmd = ["/usr/bin/env", f"PYTHONPATH={ROOT / 'src'}", f"NACRE_DSN_APP={d['app']}", f"NACRE_DSN_AUTH={d['auth']}",
           f"NACRE_ROOT_KEY_DIR={world['root']}", f"NACRE_TOKEN_KEY_FILE={world['key_file']}",
           sys.executable, "-B", "-m", "nacre.interface.mcp_server", "--tau-strong-q", "6000", "--config-version", "t"]
    c = Client(cmd, world["token"])
    out = _decide(c, world["a"])
    assert out["trust_basis"] == "verified" and out["created"] is True
    assert world["token"] not in " ".join(cmd)


def test_local_mode_writes_asserted_and_outcomes_keep_an_unknown_success(world):
    c = Client.local(world["services"], world["pid"])
    d = _decide(c, world["a"])
    assert d["trust_basis"] == "asserted"
    o = c.record_outcome(world["a"], str(uuid.uuid4()), uuid.UUID(d["event_id"]), None, [("status", "unknown")])
    assert o["created"] is True
    with pytest.raises(NacreError) as e:
        _decide(c, world["a"], stakes=["no-such-tag"])
    assert e.value.code == "invalid_request"


@pytest.mark.parametrize("url", ["http://10.0.0.5/mcp", "http://example.com/mcp", "ftp://127.0.0.1/mcp", "nope"])
def test_a_token_is_never_sent_over_plain_http_to_a_non_loopback_host(url):
    with pytest.raises(NacreError, match="^invalid_request"):
        Client(url, "t")


def test_https_and_loopback_http_are_accepted():
    Client("https://nacre.example/mcp", "t")
    Client("http://localhost:1/mcp", "t")
    with pytest.raises(NacreError):
        Client([], "t")
