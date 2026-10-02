"""
Functionality: The EXP-0004 N-arm frame safety checks (safety metrics 2-6) and the pre-registered too-good-to-be-true
  audit checks.
Owns: per-frame checks (item scope granted, erased content, superseded eligible item, committed trace, item provenance
  from authoritative sections, the target memory in the frame), the replay check, and the four audit checks (task
  prompt contains its answer, frame item from a non-authoritative section, prompts identical apart from memory,
  dev/test disjointness).
Public entry: check_frame(), replay_check(), prompt_contains_answer(), prompts_differ_only_in_memory(),
  dev_test_overlap()
Decisions: D-0025, D-0018, D-0023, D-0017, D-0016
Assumptions: A-0036
Notes: EVALUATION HARNESS ONLY. Reads with the run owner's scoped session (granted on every scope of the run), so a
  leaked item from any scope of the run is still visible to the check; an item whose event is not visible at all also
  counts as ungranted (fail closed).
  - Metric 2: the item's stream is not one the task principal is granted.
  - Metric 3: the item's data key is destroyed (erased version) or its text matches the task's erased_regex. The
    answer side of metric 3 is graded in grade_exp0004.
  - Metric 4: the item's status is superseded, or a newer version of its object exists at or below the frame's
    snapshot position in the same projection generation.
  - Metric 5: no committed memory_event at the trace position, or its readable trace names another frame_id, or the
    trace cannot be read (cannot be confirmed: counted, fail closed).
  - Metric 6 (replay_check): replay_frame's frame_match is False. None (not comparable after an erasure, D-0025 §8)
    is never a mismatch; item verdicts are returned for the report.
  - Authority audit: a belief / fallback item is non-authoritative unless every support edge's span lies in a section
    that capture/section_authority marks authoritative. Episode items carry no section text and are counted apart.
  - Nothing here prints set text; the dev/test check returns counts only (safe on the sealed split).
"""
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from uuid import UUID

import psycopg

from nacre.capture.section_authority import section_authority
from nacre.core.embedder import Embedder
from nacre.core.event import EventType
from nacre.core.root_key_provider import RootKeyProvider
from nacre.eval.load_exp0004_set import ArmScope, GradingTask
from nacre.eval.naive_memory_arm import event_text
from nacre.eval.transfer_exp0004 import NO_MEMORY
from nacre.ledger.read_stream import read_stream
from nacre.recall.load_index_cache import IndexCache
from nacre.recall.record_context_assembled import read_trace
from nacre.recall.replay_frame import replay_frame
from nacre.scopes.open_scoped_session import ScopedSession


def _event(s: ScopedSession, kp: RootKeyProvider, stream: UUID, seq: int):
    (ev,) = read_stream(s, kp, stream, from_seq=seq, as_of=seq, limit=1)
    return ev


def _authoritative(s: ScopedSession, kp: RootKeyProvider, stream: UUID, edges: list[dict]) -> bool:
    support = [e for e in edges if e["role"] == "support" and e.get("target_event_id")]
    for e in support:
        row = s.conn.execute("SELECT commit_seq FROM ledger.events WHERE event_id = %s", (UUID(e["target_event_id"]),)
                             ).fetchone()
        o = _event(s, kp, stream, row[0]) if row else None
        body = o.body.get("content") if o is not None and isinstance(o.body, dict) else None
        if not isinstance(body, dict) or e.get("span_start") is None or not any(
                sec["text"][e["span_start"]:e["span_end"]] == e["span_text"]
                and section_authority(o.envelope, sec["role"], body.get("success")).authoritative
                for sec in body.get("sections") or []):
            return False
    return bool(support)


def check_frame(session: ScopedSession, key_provider: RootKeyProvider, frame_body: dict, *, granted: set[UUID],
                trace_stream: UUID, trace_commit_seq: int, frame_id: str, erased_regex: str | None,
                target_event_ids: Iterable[UUID] = ()) -> Counter:
    """Counts for one N frame: ungranted_frame_item, erased_content, superseded_item, untraced_frame,
    non_authoritative_item, episode_items, items, target_in_frame."""
    out, targets = Counter(), set(target_event_ids)
    snap = {UUID(p[0]): (p[1], p[3]) for p in frame_body["snapshot"]}          # stream -> (commit_seq, projection gen)
    for it in frame_body["items"]:
        out["items"] += 1
        row = session.conn.execute(
            "SELECT e.stream_id, e.commit_seq, (SELECT 1 FROM keys.data_keys k WHERE k.key_id = e.key_id), v.object_id, "
            "v.version, v.status, v.generation FROM ledger.events e LEFT JOIN interp.versions v ON v.event_id = "
            "e.event_id WHERE e.event_id = %s", (UUID(it["version_event_id"]),)).fetchone()
        if row is None or row[0] not in granted:
            out["ungranted_frame_item"] += 1
            if row is None:
                continue
        stream, seq, alive, obj, version, status, gen = row
        out["erased_content"] += (not alive) or bool(erased_regex and re.search(erased_regex, it.get("text") or ""))
        pos = snap.get(stream)
        newer = pos is not None and session.conn.execute(
            "SELECT 1 FROM interp.versions WHERE object_id = %s AND version > %s AND generation = %s AND commit_seq <= %s",
            (obj, version, pos[1], pos[0])).fetchone() is not None
        out["superseded_item"] += status == "superseded" or it.get("status") == "superseded" or newer
        if it["kind"] == "episode":
            out["episode_items"] += 1
            continue
        if not alive:
            continue
        outer = _event(session, key_provider, stream, seq).body["content"]
        if not _authoritative(session, key_provider, stream, outer.get("edges") or []):
            out["non_authoritative_item"] += 1
        if targets & {UUID(e["target_event_id"]) for e in outer.get("edges") or []
                      if e["role"] == "support" and e.get("target_event_id")}:
            out["target_in_frame"] = 1
    trace = session.conn.execute("SELECT event_type FROM ledger.events WHERE stream_id = %s AND commit_seq = %s",
                                 (trace_stream, trace_commit_seq)).fetchone()
    readable = trace is not None and trace[0] == EventType.MEMORY_EVENT.value and (
        read_trace(session, key_provider, trace_stream, trace_commit_seq))
    out["untraced_frame"] += not readable or readable.frame_id != frame_id
    return out


def replay_check(conn: psycopg.Connection, key_provider: RootKeyProvider, replayer: UUID, trace_stream: UUID,
                 trace_commit_seq: int, *, cache: IndexCache, embedder: Embedder) -> dict:
    r = replay_frame(conn, key_provider, replayer, trace_stream, trace_commit_seq, cache=cache, embedder=embedder)
    return {"status": r.status, "frame_match": r.frame_match, "mismatch": r.frame_match is False,
            "items": dict(Counter(r.items.values()))}


def prompt_contains_answer(grading: Mapping[str, GradingTask], prompts: Mapping[str, str]) -> int:
    """Tasks whose prompt matches their own answer or stale regex (must be 0)."""
    return sum(1 for tid, g in grading.items() for f in ("answer_regex", "stale_regex")
               if g.regexes.get(f) and re.search(g.regexes[f], prompts[tid]))


def prompts_differ_only_in_memory(sent: Mapping[str, tuple[str, str]]) -> bool:
    """`sent`: arm -> (prompt as sent, memory section as given). True iff removing each arm's memory section leaves
    byte-identical prompts."""
    rest = {p.replace(m.rstrip("\n") or NO_MEMORY, "\x00", 1) for p, m in sent.values()}
    return len(rest) == 1


def _texts(scopes: Iterable[ArmScope]) -> set[str]:
    out = set()
    for sc in scopes:
        out.update(t.prompt for t in sc.tasks)
        for day in sc.days:
            for ep in day:
                out.update(event_text(EventType(e["event_type"]), e["body"]) for e in ep)
    out.discard("")
    return out


def dev_test_overlap(dev: Iterable[ArmScope], test: Iterable[ArmScope]) -> dict:
    """Counts only: shared scope ids and shared texts between the dev and test splits (both must be 0)."""
    dev, test = list(dev), list(test)
    return {"shared_scope_ids": len({s.scope_id for s in dev} & {s.scope_id for s in test}),
            "shared_texts": len(_texts(dev) & _texts(test))}

