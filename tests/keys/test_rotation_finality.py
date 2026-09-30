"""A-0008 as specified by the owner (D-0004 amendments 6-7, D-0014): shredded keys recovered from a REAL backup
(pg_dump) can never be unwrapped after master then root rotation; rotations are crash-safe and resumable."""
import re
import subprocess
import uuid
from datetime import UTC, date, datetime, timedelta

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

from nacre.core.event import ActorKind, EventType, PayloadType, Source
from nacre.core.root_key_provider import WrappedKey
from nacre.keys import rotate_master_key as rmk
from nacre.keys.decrypt_payload import Shredded
from nacre.keys.execute_due_shreds import execute_due_shreds
from nacre.keys.get_or_create_key import KeyResolutionError, unwrap_data_key
from nacre.keys.keyadmin_session import keyadmin_transaction
from nacre.keys.manage_shred_requests import ShredKind, request_shred
from nacre.keys.rotate_master_key import RotationError, run_master_rotations
from nacre.keys.rotate_root_key import rotate_root_key
from nacre.ledger.append_event import AppendRequest, Authorship, append_event
from nacre.ledger.read_stream import read_stream
from nacre.scopes.register_scope import ScopeKind, register_scope
from nacre.scopes.set_access import set_access

NOW = datetime.now(UTC)
OPERATOR = uuid.UUID(int=424242)
CONFIRM = "old root version backup destroyed: ops ticket TEST-1"


def pg_dump_keys(admin_dsn):
    """A real backup: pg_dump of the key tables from the Docker Postgres, parsed from --column-inserts."""
    db = conninfo_to_dict(admin_dsn)["dbname"]
    out = subprocess.run(["docker", "compose", "exec", "-T", "postgres", "pg_dump", "-U", "postgres", "-d", db,
                          "--data-only", "--column-inserts", "-t", "keys.stream_master_keys", "-t", "keys.data_keys"],
                         capture_output=True, text=True, check=True).stdout
    rows = {"stream_master_keys": [], "data_keys": []}
    for m in re.finditer(r"INSERT INTO keys\.(\w+) \(([^)]*)\) VALUES \((.*)\);", out):
        cols = [c.strip() for c in m.group(2).split(",")]
        vals = [a if a else b for a, b in re.findall(r"'((?:[^']|'')*)'|(-?\d+)", m.group(3))]
        rows[m.group(1)].append(dict(zip(cols, vals)))
    return rows


@pytest.fixture
def world(org, provider, migrated_db):
    org_id, owner, open_ = org
    proj, person = uuid.uuid4(), uuid.uuid4()
    with open_(owner) as s:
        register_scope(s, provider, org_id=org_id, stream_id=proj, kind=ScopeKind.PROJECT, idempotency_key=str(uuid.uuid4()))
    with open_(owner) as s:
        set_access(s, provider, org_id=org_id, principal_id=owner, stream_id=proj, can_read=True, can_append=True,
                   idempotency_key=str(uuid.uuid4()))

    def write(actor_kind, actor, text, event_type=EventType.STATEMENT):
        with open_(owner) as s:
            return append_event(s, provider, AppendRequest(
                stream_id=proj, event_type=event_type, payload_type=PayloadType.TEXT, actor_kind=actor_kind,
                actor_id=actor, source=Source.CHAT, authorship=Authorship.SCOPE_PRINCIPAL,
                idempotency_key=str(uuid.uuid4()), content=text)).envelope
    said = write(ActorKind.PERSON, person, "my words")
    other = write(ActorKind.PERSON, uuid.uuid4(), "someone else's words")
    ran = write(ActorKind.AGENT, uuid.uuid4(), "ran tests", EventType.ACTION)
    ka = lambda: psycopg.connect(migrated_db["keyadmin"])  # noqa: E731
    return dict(org=org_id, owner=owner, open=open_, proj=proj, person=person, said=said, other=other, ran=ran, ka=ka,
                admin=migrated_db["admin"])


def _shred(w, provider, kind, **kw):
    with w["ka"]() as c, keyadmin_transaction(c) as tx:
        request_shred(tx, provider, org_id=w["org"], requester=w["owner"], kind=kind,
                      idempotency_key=str(uuid.uuid4()), now=NOW, **kw)
    execute_due_shreds(w["ka"](), provider, now=NOW + timedelta(days=8))


def _bodies(w, provider):
    with w["open"](w["owner"]) as s:
        return {e.envelope.event_id: e.body for e in read_stream(s, provider, w["proj"])}


def test_person_erasure_is_final_after_master_then_root_rotation(world, provider):
    backup = pg_dump_keys(world["admin"])
    person_dek = next(r for r in backup["data_keys"] if uuid.UUID(r["subject_id"]) == world["person"])
    old_master = next(r for r in backup["stream_master_keys"] if uuid.UUID(r["stream_id"]) == world["proj"])
    _shred(world, provider, ShredKind.ERASE_PERSON, person_id=world["person"])
    assert run_master_rotations(world["ka"](), provider, operator=OPERATOR) == [world["proj"]]
    result = rotate_root_key(world["ka"](), provider, operator=OPERATOR, backup_destroyed_confirmation=CONFIRM)
    assert old_master["root_key_version"] in result.destroyed_versions
    # 1. the old master from the backup can never be unwrapped: its root version is destroyed
    with pytest.raises(KeyError, match="not held"):
        provider.unwrap(WrappedKey(old_master["root_key_version"], bytes.fromhex(old_master["wrapped_key"][2:])),
                        world["proj"].bytes)
    # 2. the deleted data key from the backup does not unwrap under the CURRENT (rotated) master either
    with psycopg.connect(world["admin"]) as c:
        version, wrapped = c.execute("SELECT root_key_version, wrapped_key FROM keys.stream_master_keys WHERE stream_id = %s",
                                     (world["proj"],)).fetchone()
    current_master = provider.unwrap(WrappedKey(version, bytes(wrapped)), world["proj"].bytes)
    with pytest.raises(KeyResolutionError):
        unwrap_data_key(current_master, uuid.UUID(person_dek["key_id"]), world["proj"], world["person"],
                        date.fromisoformat(person_dek["month"]), bytes.fromhex(person_dek["wrapped_key"][2:]))
    # 3. everything that was not erased still reads
    bodies = _bodies(world, provider)
    assert bodies[world["said"].event_id] == Shredded(world["said"].key_id)
    assert bodies[world["other"].event_id]["content"] == "someone else's words"
    assert bodies[world["ran"].event_id]["content"] == "ran tests"


def test_scope_deletion_is_final_after_root_rotation(world, provider):
    backup = pg_dump_keys(world["admin"])
    old_master = next(r for r in backup["stream_master_keys"] if uuid.UUID(r["stream_id"]) == world["proj"])
    _shred(world, provider, ShredKind.DELETE_SCOPE, stream_id=world["proj"])
    rotate_root_key(world["ka"](), provider, operator=OPERATOR, backup_destroyed_confirmation=CONFIRM)
    with pytest.raises(KeyError):
        provider.unwrap(WrappedKey(old_master["root_key_version"], bytes.fromhex(old_master["wrapped_key"][2:])),
                        world["proj"].bytes)


def test_root_rotation_refuses_while_a_master_rotation_is_pending(world, provider):
    _shred(world, provider, ShredKind.ERASE_PERSON, person_id=world["person"])
    with pytest.raises(RotationError, match="master rotations first"):
        rotate_root_key(world["ka"](), provider, operator=OPERATOR, backup_destroyed_confirmation=CONFIRM)
    run_master_rotations(world["ka"](), provider, operator=OPERATOR)
    rotate_root_key(world["ka"](), provider, operator=OPERATOR, backup_destroyed_confirmation=CONFIRM)
    assert run_master_rotations(world["ka"](), provider, operator=OPERATOR) == []            # one per cycle


def test_root_rotation_needs_the_operator_confirmation(world, provider):
    with pytest.raises(RotationError, match="confirm"):
        rotate_root_key(world["ka"](), provider, operator=OPERATOR, backup_destroyed_confirmation=" ")


def test_a_crash_mid_master_rotation_loses_no_surviving_key(world, provider, monkeypatch):
    _shred(world, provider, ShredKind.ERASE_PERSON, person_id=world["person"])
    real, calls = rmk.wrap_data_key, []

    def crash_on_second(*a, **kw):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("killed mid-rotation")
        return real(*a, **kw)
    monkeypatch.setattr(rmk, "wrap_data_key", crash_on_second)
    with pytest.raises(RuntimeError, match="killed"):
        run_master_rotations(world["ka"](), provider, operator=OPERATOR)
    monkeypatch.setattr(rmk, "wrap_data_key", real)
    bodies = _bodies(world, provider)                                    # nothing lost: rolled back whole
    assert bodies[world["other"].event_id]["content"] == "someone else's words"
    assert run_master_rotations(world["ka"](), provider, operator=OPERATOR) == [world["proj"]]  # resumes
    assert _bodies(world, provider)[world["ran"].event_id]["content"] == "ran tests"


def test_a_crash_mid_root_rotation_resumes_and_loses_nothing(world, provider, monkeypatch):
    with world["open"](world["owner"]) as s:                             # a second stream with a master key
        second = uuid.uuid4()
        register_scope(s, provider, org_id=world["org"], stream_id=second, kind=ScopeKind.TEAM,
                       idempotency_key=str(uuid.uuid4()))
    old_version = provider.current_version()
    real, calls = provider.wrap, []

    def crash_on_second(*a, **kw):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("killed mid-batch")
        return real(*a, **kw)
    monkeypatch.setattr(provider, "wrap", crash_on_second)
    with pytest.raises(RuntimeError, match="killed"):
        rotate_root_key(world["ka"](), provider, operator=OPERATOR, backup_destroyed_confirmation=CONFIRM, batch_size=1)
    monkeypatch.setattr(provider, "wrap", real)
    crashed_run_version = provider.current_version()
    result = rotate_root_key(world["ka"](), provider, operator=OPERATOR, backup_destroyed_confirmation=CONFIRM,
                             batch_size=1, resume=True)
    assert result.new_version == crashed_run_version == provider.current_version()   # continued, not restarted
    with psycopg.connect(world["admin"]) as c:
        masters = c.execute("SELECT count(*) FROM keys.stream_master_keys").fetchone()[0]
    assert old_version in result.destroyed_versions and result.rewrapped == masters - 1   # first batch was kept
    assert _bodies(world, provider)[world["ran"].event_id]["content"] == "ran tests"


def test_rotations_are_audited_in_the_org_stream(world, provider):
    _shred(world, provider, ShredKind.ERASE_PERSON, person_id=world["person"])
    run_master_rotations(world["ka"](), provider, operator=OPERATOR)
    rotate_root_key(world["ka"](), provider, operator=OPERATOR, backup_destroyed_confirmation=CONFIRM)
    with world["open"](world["owner"]) as s:
        ops = [e.body["content"] for e in read_stream(s, provider, world["org"]) if isinstance(e.body["content"], dict)]
    assert [o["op"] for o in ops if o["op"] in ("shred_request", "shred_executed", "master_rotated", "root_rotated")] \
        == ["shred_request", "shred_executed", "master_rotated", "root_rotated"]
    assert ops[-1]["backup_destroyed_confirmation"] == CONFIRM


def test_a_scope_erased_then_deleted_is_not_rotated(world, provider):
    _shred(world, provider, ShredKind.ERASE_PERSON, person_id=world["person"])
    _shred(world, provider, ShredKind.DELETE_SCOPE, stream_id=world["proj"])
    assert run_master_rotations(world["ka"](), provider, operator=OPERATOR) == []     # nothing left to rotate


def test_a_master_key_left_on_an_old_version_blocks_destruction(world, provider, monkeypatch):
    with psycopg.connect(world["admin"]) as c:                           # a rewrap that silently keeps the old one
        original = {s.bytes: WrappedKey(v, bytes(w)) for s, v, w in
                    c.execute("SELECT stream_id, root_key_version, wrapped_key FROM keys.stream_master_keys")}
    monkeypatch.setattr(provider, "wrap", lambda key, aad: original[aad])
    with pytest.raises(RotationError, match="still on an old root version"):
        rotate_root_key(world["ka"](), provider, operator=OPERATOR, backup_destroyed_confirmation=CONFIRM)
    monkeypatch.undo()
    assert _bodies(world, provider)[world["ran"].event_id]["content"] == "ran tests"   # old version still held


def test_an_unreferenced_current_version_is_destroyed_too(world, provider):
    unused = provider.create_version()                                   # no master key was ever wrapped under it
    result = rotate_root_key(world["ka"](), provider, operator=OPERATOR, backup_destroyed_confirmation=CONFIRM)
    assert unused in result.destroyed_versions
