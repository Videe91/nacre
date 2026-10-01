"""The runner's --rebuild-check (gate item 8 on real state) reports identical scopes and catches a forged row."""
import importlib.util
import uuid
from contextlib import contextmanager
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("runner", ROOT / "scripts" / "run_exp0003.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def test_rebuild_check_reports_identical_and_detects_a_forged_row(session, streams, provider):
    a = streams["a"]
    p = uuid.uuid4()

    @contextmanager
    def open_session():
        with session(p, read=[a], write=[a]) as s:
            yield s
    from nacre.core.event import ActorKind, EventType, PayloadType, Source
    from nacre.ledger.append_event import AppendRequest, Authorship, append_event
    with open_session() as s:
        env = append_event(s, provider, AppendRequest(
            stream_id=a, event_type=EventType.MEMORY_EVENT, payload_type=PayloadType.STRUCTURED, actor_kind=ActorKind.SYSTEM,
            actor_id=uuid.UUID(int=1), source=Source.SYSTEM, authorship=Authorship.SCOPE_PRINCIPAL,
            idempotency_key=str(uuid.uuid4()), content={"op": "note"})).envelope
    (ok,) = runner._rebuild_check(open_session, provider, [a])
    assert ok["identical"] and ok["versions"] == 0
    with psycopg.connect(streams["dsn"]["admin"]) as c:
        c.execute("INSERT INTO interp.versions (object_id, version, kind, status, support, stream_id, event_id, commit_seq, "
                  "content_mac) VALUES (%s,1,'belief','active','quorum',%s,%s,%s,%s)", (uuid.uuid4(), a, env.event_id, env.commit_seq, b"\x00" * 32))
    (bad,) = runner._rebuild_check(open_session, provider, [a])
    assert not bad["identical"] and len(bad["differences"]) == 1
