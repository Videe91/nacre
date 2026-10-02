"""
Functionality: Load one frozen EXP-0004 split and separate it into what the arms may see and what only the grader sees.
Owns: the sha256 check against the frozen-set table, the arm view (histories, grants, erase instructions, task prompts
  and addresses) with every grading field stripped, and the grading view (task type, erasure flag, expected ask,
  the five regexes, grading refs).
Public entry: load_split(), split_views(), ArmScope, ArmTask, GradingTask, TEST_SHA256, DEV_SHA256, REGEX_FIELDS
Decisions: D-0016, D-0025
Assumptions: none
Notes: EVALUATION HARNESS ONLY (EXP-0004 "The set"; generator docstring: grading refs "must never be given to any
  arm"). The arm view is built by copying ONLY whitelisted fields (never by deleting known grading fields), so a new
  grading field in the data can never leak by default. Each event keeps only the capture fields the harness maps
  (event_id, event_type, actor_kind, author, source, authorship, trust, body, refs, addresses).
  - The sha256 constants are the "Frozen set" table of the EXP-0004 file (a test re-reads the table).
  - The sealed test split is never printed: this file returns data structures only and its errors carry no text.
"""
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

TEST_SHA256 = "93e1b6894cb42be54e4c97637ba2a2efd012f701cd16bd51e5837f086fabcbe6"
DEV_SHA256 = "0e4874d9233ea8bbf92ee1b651e633657abd1fea961c79831bbd0401801bcf38"
REGEX_FIELDS = ("answer_regex", "stale_regex", "injection_regex", "cross_scope_regex", "erased_regex")
_EVENT_FIELDS = ("event_id", "event_type", "actor_kind", "author", "source", "authorship", "trust", "body", "refs",
                 "addresses")


class SetIntegrityError(ValueError):
    """The split file does not match its frozen sha256, or does not have the frozen shape."""


@dataclass(frozen=True)
class ArmTask:
    task_id: str
    prompt: str
    addresses: tuple[str, ...]


@dataclass(frozen=True)
class ArmScope:
    scope_id: str
    set_name: str
    principal: str
    granted_scopes: tuple[str, ...]
    persons: tuple[str, ...]
    erase_persons: tuple[str, ...]
    days: tuple[tuple[tuple[dict, ...], ...], ...]      # day -> episode -> events
    tasks: tuple[ArmTask, ...]


@dataclass(frozen=True)
class GradingTask:
    task_id: str
    scope_id: str
    set_name: str
    type: str                                            # T1 | T2 | T3 | T5
    erasure_target: bool
    expected_ask: bool
    regexes: dict                                        # REGEX_FIELDS -> pattern or None
    refs: dict                                           # grading_refs (dataset event ids; never given to an arm)

    @property
    def category(self) -> str:
        """The fixed EXP-0004 category: E for an erasure target, else the task type."""
        return "E" if self.erasure_target else self.type


def _event(ev: dict) -> dict:
    return json.loads(json.dumps({k: ev[k] for k in _EVENT_FIELDS}))      # a deep copy of whitelisted fields only


def split_views(data: dict) -> tuple[list[ArmScope], dict[str, GradingTask]]:
    """(arm view per scope, grading view per task_id) of one parsed split."""
    scopes, grading = [], {}
    for sc in data["scopes"]:
        days = tuple(tuple(tuple(_event(e) for e in ep["events"]) for ep in day["episodes"])
                     for day in sorted(sc["days"], key=lambda d: d["day"]))
        tasks = tuple(ArmTask(str(t["task_id"]), str(t["prompt"]), tuple(str(a) for a in t.get("addresses") or ()))
                      for t in sc["tasks"])
        scopes.append(ArmScope(str(sc["scope_id"]), str(sc["set"]), str(sc["principal"]["id"]),
                               tuple(str(s) for s in sc["principal"]["granted_scopes"]),
                               tuple(str(p["id"]) for p in sc["persons"]), tuple(str(p) for p in sc["erase_persons"]),
                               days, tasks))
        for t in sc["tasks"]:
            if t["task_id"] in grading:
                raise SetIntegrityError("duplicate task id")
            grading[t["task_id"]] = GradingTask(str(t["task_id"]), str(sc["scope_id"]), str(sc["set"]), str(t["type"]),
                                                bool(t["erasure_target"]), bool(t["expected_ask"]),
                                                {f: t.get(f) for f in REGEX_FIELDS},
                                                json.loads(json.dumps(t.get("grading_refs") or {})))
    return scopes, grading


def load_split(path: Path, expected_sha256: str) -> tuple[list[ArmScope], dict[str, GradingTask]]:
    """Verify the file's sha256 against its frozen value, then return its two views."""
    raw = Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise SetIntegrityError(f"{Path(path).name}: sha256 does not match the frozen value")
    return split_views(json.loads(raw))
