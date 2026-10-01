"""
Functionality: Answer model requests only from recorded `result` events: exact replay, never a live call.
Owns: indexing replayable recordings (status ok, not redacted) by request hash in commit order, serving identical
  requests in their recorded order, and failing loudly on a miss.
Public entry: RecordedProvider, RecordingMiss
Decisions: D-0022, D-0016
Assumptions: A-0025
Notes: D-0022 recorded mode for everyday tests and the gate's determinism check (Phase 2 gate item 4). It reads
  through a scoped session, so a recording is only replayable where its scope is readable (SI-5). It never imports
  a provider SDK and never touches the network (SI-6). A miss raises RecordingMiss; there is no live fallback.
"""
from collections import defaultdict, deque
from uuid import UUID

from nacre.core.event import ActorKind, EventType
from nacre.core.model_provider import ModelRequest, ModelResponse, Usage, request_sha256
from nacre.core.root_key_provider import RootKeyProvider
from nacre.ledger.read_stream import read_stream
from nacre.scopes.open_scoped_session import ScopedSession


class RecordingMiss(LookupError):
    """No unused recording matches this exact request."""


class RecordedProvider:
    replay = True
    name = "recorded"

    def __init__(self, session: ScopedSession, key_provider: RootKeyProvider, stream_ids: list[UUID]):
        self._queues: dict[str, deque] = defaultdict(deque)
        self.recordings = 0
        for stream in stream_ids:
            for e in read_stream(session, key_provider, stream):
                c = e.body.get("content") if isinstance(e.body, dict) else None
                if (e.envelope.event_type == EventType.RESULT and e.envelope.actor_kind == ActorKind.MODEL
                        and isinstance(c, dict) and c.get("kind") == "model_call" and c.get("status") == "ok"
                        and not c.get("redacted")):
                    self._queues[c["request_sha256"]].append(c["response"])
                    self.recordings += 1

    def complete(self, request: ModelRequest, *, timeout_s: float) -> ModelResponse:
        queue = self._queues.get(request_sha256(request))
        if not queue:
            raise RecordingMiss(f"no unused recording for {request.purpose} request {request_sha256(request)[:12]}")
        r = queue.popleft()
        u = r["usage"]
        return ModelResponse(text=r["text"], finish_reason=r["finish_reason"],
                             usage=Usage(u["input_tokens"], u["output_tokens"], u["cached_input_tokens"]),
                             response_id=r["response_id"], model_reported=r["model_reported"], latency_ms=0)
