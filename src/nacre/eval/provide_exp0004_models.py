"""
Functionality: The three EXP-0004 model modes (live, dry, recorded replay): which provider each seat uses, how a
  scope's recordings are loaded before its first sleep pass, and how they are exported after a live run.
Owns: LiveMode (a live or dry provider; exports every scope's recordings as fixtures), RecordedMode (loads a live run's
  fixtures and replays them with zero network; any live call is a hard failure), DryProvider and NoLiveCalls, and the
  two fixture parts per scope (history before erasure, tasks after).
Public entry: LiveMode, RecordedMode, DryProvider, NoLiveCalls, fixture_path()
Decisions: D-0022, D-0021, D-0016, D-0023
Assumptions: A-0025
Notes: EVALUATION HARNESS ONLY (EXP-0004 order of work, step 6: dry run plus `--recorded` replay; D-0022 recorded mode).
  - Fixtures: two JSONL per (replicate, scope), written by models/load_model_call_fixtures.export_model_calls (commit
    order; ok and unredacted calls only): <dir>/rep<k>/<scope_id>.history.jsonl right BEFORE erase_person (the sleep
    calls of an erased person's episodes are keyed under that person and become unreadable after it), and
    <dir>/rep<k>/<scope_id>.tasks.jsonl after the tasks (only the transfer calls, purpose eval.*).
    D1, flagged to the owner: the history fixture therefore keeps, on disk in the run folder (outside the repo and the
    database), recorded prompts that quote content erased later in the run. The set is synthetic; the run folder is
    the owner's to delete.
  - Recorded mode loads both parts of a scope's fixture right after its first day is captured (before the first sleep pass),
    with sources = the scope's anchor event (its first event under the stream key, never erased). The sleep pass then
    reuses those recordings (run_sleep_pass replays a recording of an identical request before any live call) and
    the transfers replay through a RecordedProvider over the scope's stream. Any request without a recording raises
    (RecordingMiss for transfers; NoLiveCalls for sleep seats): there is never a live fallback.
    D1: loaded recordings are keyed by the anchor, so in a REPLAY database they outlive erase_person; replay
    databases are dropped at the end of the run.
  - DryProvider: plumbing only, no key and no model: the sleep seats propose nothing and every transfer replies
    {"answer": null, "ask": true}. Usage is 1/1 token, so dry costs are not estimates.
"""
import json
from pathlib import Path
from uuid import UUID

from nacre.core.model_provider import ModelResponse, Usage
from nacre.core.root_key_provider import RootKeyProvider
from nacre.eval.transfer_exp0004 import TRANSFER_MODEL
from nacre.models.load_model_call_fixtures import export_model_calls, load_model_calls
from nacre.models.recorded_provider import RecordedProvider


def fixture_path(root: Path, rep: int, scope_id: str, part: str) -> Path:
    if part not in ("history", "tasks"):
        raise ValueError("a fixture part is history or tasks")
    return Path(root) / f"rep{rep}" / f"{scope_id}.{part}.jsonl"


class DryProvider:
    name, replay = TRANSFER_MODEL[0], False

    def complete(self, request, *, timeout_s):
        text = '{"propositions": []}' if request.purpose.startswith("sleep.") else '{"answer": null, "ask": true}'
        return ModelResponse(text, "completed", Usage(1, 1, None), None, TRANSFER_MODEL[1], 0)


class NoLiveCalls:
    name, replay = TRANSFER_MODEL[0], False

    def complete(self, request, *, timeout_s):
        raise AssertionError(f"recorded mode attempted a live call ({request.purpose})")


class _StreamReplay:
    """Replays the recordings in one scope's stream (read lazily, after they were loaded)."""
    name, replay = "recorded", True

    def __init__(self, open_session, key_provider, stream):
        self._open, self._kp, self._stream, self._rp = open_session, key_provider, stream, None

    def complete(self, request, *, timeout_s):
        if self._rp is None:
            with self._open() as s:
                self._rp = RecordedProvider(s, self._kp, [self._stream])
        return self._rp.complete(request, timeout_s=timeout_s)


class LiveMode:
    """A live (capped) or dry provider for every seat; exports each scope's recordings when `fixtures` is set."""
    name = "live"

    def __init__(self, provider, fixtures: Path | None = None):
        self.sleep_provider, self._fixtures = provider, fixtures

    def transfer_provider(self, open_session, key_provider: RootKeyProvider, stream: UUID):
        return self.sleep_provider

    def prepare(self, open_session, key_provider: RootKeyProvider, stream: UUID, rep: int, scope_id: str,
                anchor: UUID) -> int:
        return 0

    def _export(self, open_session, key_provider, stream, rep, scope_id, part) -> int:
        if self._fixtures is None:
            return 0
        path = fixture_path(self._fixtures, rep, scope_id, part)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open_session() as s:
            export_model_calls(s, key_provider, stream, path)
        lines = path.read_text().splitlines()
        if part == "tasks":
            lines = [x for x in lines if json.loads(x)["purpose"].startswith("eval.")]
        path.write_text("".join(x + "\n" for x in lines))
        return len(lines)

    def checkpoint(self, open_session, key_provider: RootKeyProvider, stream: UUID, rep: int, scope_id: str) -> int:
        """Before erase_person: export the history (sleep) recordings."""
        return self._export(open_session, key_provider, stream, rep, scope_id, "history")

    def finish(self, open_session, key_provider: RootKeyProvider, stream: UUID, rep: int, scope_id: str) -> int:
        """After the tasks: export the transfer recordings."""
        return self._export(open_session, key_provider, stream, rep, scope_id, "tasks")


class RecordedMode:
    """Replays a live run's fixtures; zero network."""
    name = "recorded"

    def __init__(self, fixtures: Path):
        self.sleep_provider, self._fixtures = NoLiveCalls(), Path(fixtures)

    def transfer_provider(self, open_session, key_provider: RootKeyProvider, stream: UUID):
        return _StreamReplay(open_session, key_provider, stream)

    def prepare(self, open_session, key_provider: RootKeyProvider, stream: UUID, rep: int, scope_id: str,
                anchor: UUID) -> int:
        n = 0
        for part in ("history", "tasks"):
            path = fixture_path(self._fixtures, rep, scope_id, part)
            if not path.exists():
                raise FileNotFoundError(f"no recorded {part} fixture for rep {rep}, scope {scope_id}")
            with open_session() as s:
                n += load_model_calls(s, key_provider, stream, path, sources=(anchor,))
        return n

    def checkpoint(self, open_session, key_provider: RootKeyProvider, stream: UUID, rep: int, scope_id: str) -> int:
        return 0

    def finish(self, open_session, key_provider: RootKeyProvider, stream: UUID, rep: int, scope_id: str) -> int:
        return 0
