"""Tests for eval/audit_exp0004.py: each N-arm frame check fires on its violation and stays 0 on a clean frame, the
replay check, and the too-good-to-be-true audit helpers (counts only)."""
import copy
import uuid

import pytest

from exp0004_kit import EchoFake, HashEmbedder, tiny_split
from phase2_kit import world
from nacre.core.db import DbRole, connect
from nacre.eval import audit_exp0004 as A
from nacre.eval.capture_exp0004_day import IdMap, capture_day
from nacre.eval.load_exp0004_set import ArmScope, ArmTask, GradingTask, split_views
from nacre.recall.load_index_cache import IndexCache
from nacre.recall.recall_context import RecallRequest, recall_context
from nacre.sleep.run_sleep_pass import run_sleep_pass


@pytest.fixture
def framed(org, provider, migrated_db, monkeypatch):
    """One tiny scope captured and slept, and one real N recall (frame + committed trace) by its agent."""
    emb = HashEmbedder()
    monkeypatch.setattr("nacre.recall.index_version.default_embedder", lambda: emb)
    w = world(org, provider)
    sc = split_views(tiny_split())[0][0]
    ids = IdMap(uuid.uuid4())
    agent = ids.actor(sc.principal)
    stream = w["new_scope"](also=(agent,))
    for day in sc.days:
        with w["session"]() as s:
            capture_day(s, provider, stream, day, ids)
        run_sleep_pass(w["session"], provider, EchoFake(), stream)
    cache = IndexCache(provider, dim=emb.dim)
    with connect(DbRole.APP, dsn=migrated_db["app"]) as conn:
        r = recall_context(conn, provider, agent, RecallRequest(stream, (("project", stream),),
                                                                "How many attempts may the queue worker of queue.py make?"),
                           cache=cache, embedder=emb, tau_strong_q=6000, config_version="t")
    assert r.frame.body["items"]
    return dict(w=w, r=r, stream=stream, ids=ids, agent=agent, cache=cache, emb=emb, dsn=migrated_db)


def _check(f, provider, body=None, **kw):
    args = dict(granted={f["stream"]}, trace_stream=f["r"].trace_stream, trace_commit_seq=f["r"].trace_commit_seq,
                frame_id=f["r"].frame.frame_id, erased_regex=None, target_event_ids=())
    args.update(kw)
    with f["w"]["session"]() as s:
        return A.check_frame(s, provider, body or f["r"].frame.body, **args)


VIOLATIONS = ("ungranted_frame_item", "erased_content", "superseded_item", "untraced_frame", "non_authoritative_item")


def test_a_clean_frame_has_no_violation_and_finds_its_target(framed, provider):
    out = _check(framed, provider, target_event_ids=[framed["ids"].events["x-s1-01:0006"]])
    assert all(out[k] == 0 for k in VIOLATIONS) and out["items"] == len(framed["r"].frame.body["items"])
    assert out["target_in_frame"] == 1


def test_an_item_from_an_ungranted_scope_is_counted(framed, provider):
    assert _check(framed, provider, granted=set())["ungranted_frame_item"] == len(framed["r"].frame.body["items"])


def test_erased_content_by_regex_is_counted(framed, provider):
    text = framed["r"].frame.body["items"][0]["text"]
    assert _check(framed, provider, erased_regex=text.split()[0])["erased_content"] >= 1


def test_a_superseded_item_is_counted(framed, provider):
    body = copy.deepcopy(framed["r"].frame.body)
    body["items"][0]["status"] = "superseded"
    assert _check(framed, provider, body)["superseded_item"] == 1


@pytest.mark.parametrize("change", [dict(frame_id="0" * 64), dict(trace_commit_seq=1)])
def test_a_frame_without_its_committed_trace_is_counted(framed, provider, change):
    assert _check(framed, provider, **change)["untraced_frame"] == 1


def test_the_replay_of_a_clean_frame_matches(framed, provider):
    with connect(DbRole.APP, dsn=framed["dsn"]["app"]) as conn:
        out = A.replay_check(conn, provider, framed["agent"], framed["r"].trace_stream, framed["r"].trace_commit_seq,
                             cache=framed["cache"], embedder=framed["emb"])
    assert out["frame_match"] is True and not out["mismatch"] and set(out["items"]) == {"verified"}


def _g(tid, **rx):
    regexes = dict.fromkeys(("answer_regex", "stale_regex", "injection_regex", "cross_scope_regex", "erased_regex"))
    regexes.update(rx)
    return GradingTask(tid, "s", "S1", "T1", False, False, regexes, {})


def test_a_prompt_that_contains_its_answer_is_counted():
    grading = {"a": _g("a", answer_regex=r"7 attempts"), "b": _g("b", answer_regex=r"100 ms", stale_regex="900 ms")}
    assert A.prompt_contains_answer(grading, {"a": "Use 7 attempts?", "b": "Which timeout?"}) == 1
    assert A.prompt_contains_answer(grading, {"a": "How many?", "b": "Was it 900 ms?"}) == 1


def test_prompts_must_differ_only_in_their_memory_section():
    from nacre.eval.transfer_exp0004 import transfer_prompt
    ok = {arm: (transfer_prompt("Q", m), m) for arm, m in (("C", "(none)"), ("V", "1. a"), ("N", "Relevant:\n1. b\n"))}
    assert A.prompts_differ_only_in_memory(ok)
    bad = dict(ok, V=(transfer_prompt("Q?", "1. a"), "1. a"))
    assert not A.prompts_differ_only_in_memory(bad)


def test_dev_test_overlap_counts_shared_ids_and_texts_only():
    scopes, _ = split_views(tiny_split())
    other = ArmScope("y-1", "S1", "y-agent", ("y-1",), (), (), scopes[0].days[:1], (ArmTask("y:t", "fresh?", ()),))
    out = A.dev_test_overlap(scopes, [other])
    assert out["shared_scope_ids"] == 0 and out["shared_texts"] > 0 and set(out) == {"shared_scope_ids", "shared_texts"}
    fresh = ArmScope("z-1", "S1", "z-agent", ("z-1",), (), (), (), (ArmTask("z:t", "a prompt nobody else has", ()),))
    assert A.dev_test_overlap(scopes, [fresh]) == {"shared_scope_ids": 0, "shared_texts": 0}
    assert A.dev_test_overlap(scopes, [scopes[0]])["shared_scope_ids"] == 1
