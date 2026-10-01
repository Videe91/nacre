"""
Functionality: Export a stream's recorded model calls as a fixture file, and load a fixture into a stream as
  replayable recordings.
Owns: the fixture line format (JSONL of the replay-relevant body fields), the file's sha256, and appending loaded
  calls as `result` events in the D-0022 body shape, marked `loaded_from_fixture`.
Public entry: export_model_calls(), load_model_calls(), fixture_sha256()
Decisions: D-0022, D-0016
Assumptions: A-0025
Notes: Recorded mode (D-0022): everyday tests and the gate's determinism check (Phase 2 gate item 4) replay a live
  run's calls. Fixtures hold synthetic task data only (frozen MNEXA families). Only successful, unredacted calls are
  exported (RecordedProvider replays nothing else). Loading writes `result` events directly, because a fixture is not
  a live call; each loaded body carries `loaded_from_fixture` (the file's sha256) so it is never mistaken for one,
  and call_model's scope, policy and price checks still guard every replay.
"""
import hashlib
import json
import uuid
from pathlib import Path
from uuid import UUID

from nacre.core.event import ActorKind, EventType, PayloadType, Source
from nacre.core.root_key_provider import RootKeyProvider
from nacre.ledger.append_event import AppendRequest, Authorship, append_event
from nacre.ledger.read_stream import read_stream
from nacre.scopes.open_scoped_session import ScopedSession

_FIELDS = ("purpose", "request", "request_sha256", "response")
_FIXTURE_ACTOR = uuid.UUID("4e3d2c1b-0a9f-4e8d-b7c6-5a4b3c2d1e0f")


def fixture_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def export_model_calls(session: ScopedSession, key_provider: RootKeyProvider, stream_id: UUID, path: Path) -> int:
    """Write the stream's replayable calls (commit order) to `path` as JSONL; return how many."""
    lines = []
    for e in read_stream(session, key_provider, stream_id):
        c = e.body.get("content") if isinstance(e.body, dict) else None
        if (e.envelope.event_type == EventType.RESULT and e.envelope.actor_kind == ActorKind.MODEL and isinstance(c, dict)
                and c.get("kind") == "model_call" and c.get("status") == "ok" and not c.get("redacted")):
            lines.append(json.dumps({k: c[k] for k in _FIELDS} | {"model": e.envelope.actor_model}, sort_keys=True))
    Path(path).write_text("".join(line + "\n" for line in lines))
    return len(lines)


def load_model_calls(session: ScopedSession, key_provider: RootKeyProvider, stream_id: UUID, path: Path) -> int:
    """Append every fixture line as a replayable `result` event in `stream_id`; return how many."""
    digest = fixture_sha256(path)
    n = 0
    for line in Path(path).read_text().splitlines():
        rec = json.loads(line)
        body = {"kind": "model_call", "status": "ok", "redacted": False, "attempt": 1, "loaded_from_fixture": digest,
                **{k: rec[k] for k in _FIELDS}}
        append_event(session, key_provider, AppendRequest(
            stream_id=stream_id, event_type=EventType.RESULT, payload_type=PayloadType.TRACE, actor_kind=ActorKind.MODEL,
            actor_id=_FIXTURE_ACTOR, source=Source.SYSTEM, authorship=Authorship.EXTERNAL,
            idempotency_key=str(uuid.uuid4()), content=body, actor_model=rec["model"],
            actor_model_version=rec["response"].get("model_reported")))
        n += 1
    return n
