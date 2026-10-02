"""Tests for recall/assemble_frame.py (R16, D-0025 §5/§7 + amendment 1): a deterministic, float-free frame whose id is
the sha256 of its CBOR; contested items labelled, carrying their contradicting evidence and placed after every
uncontested item; budgets; quorum pruning; the query text never in the frame."""
import uuid

import pytest

from recall_kit import belief, counter_episode
from nacre.interface.render_frame import render_memory_section
from nacre.recall.assemble_frame import Budget, _pruned, assemble_frame
from nacre.recall.embed_local import DIM
from nacre.recall.freeze_snapshot import freeze_snapshot
from nacre.recall.index_version import default_embedder
from nacre.recall.load_index_cache import IndexCache
from nacre.recall.merge_scopes import merge_scopes
from nacre.recall.rank_candidates import Ranked, rank_candidates
from nacre.stores.contest_belief import contest_belief
from nacre.stores.propose_contradiction import propose_contradiction

QUERY = "Should the payments release pin the base image?"


def _world(rw, provider, a):
    with rw() as s:
        disputed = belief(s, provider, a, "Pin the payments base image by digest before release.", "Pin the payments base image by digest")
        belief(s, provider, a, "Run the schema migration check before merging.", "Run the schema migration check")
        belief(s, provider, a, "Rotate the payments signing certificate weekly.", "Rotate the payments signing certificate")
        for _ in range(2):
            d = counter_episode(s, provider, a, "Never pin the payments image.")
            propose_contradiction(s, provider, stream_id=a, belief_object_id=disputed.object_id, decision_id=d,
                                  text="never pin the payments image")
        assert contest_belief(s, provider, a, disputed.object_id).status == "contested"
    return disputed


def _recall(snap, provider, a, budget=Budget()):
    cache, emb = IndexCache(provider, dim=DIM), default_embedder()
    with snap() as s:
        snapshot = freeze_snapshot(s, [a])
        scopes = [("project", a)]
        cands = merge_scopes(s, snapshot, scopes)
        entries = cache.entries(s, {a: [c.version_event_id for c in cands]})[a]
        cands = [c for c in cands if c.version_event_id in entries]
        texts = {v: e.text for v, e in entries.items()}
        ranked, active = rank_candidates(cands, texts, {v: e.embedding for v, e in entries.items()}, QUERY,
                                         emb.embed([QUERY])[0], {}, False)
        return assemble_frame(s, provider, snapshot=snapshot, scopes=scopes, principal_id=uuid.UUID(int=9),
                              query_text=QUERY, addresses=[], relaxations=[], candidates=cands, ranked=ranked,
                              active=active, texts=texts, coverage="weak", budget=budget)


def test_the_frame_is_deterministic_and_never_holds_the_query_text(rw, snap, provider, streams):
    _world(rw, provider, streams["a"])
    f1, f2 = _recall(snap, provider, streams["a"]), _recall(snap, provider, streams["a"])
    assert f1.frame_id == f2.frame_id and f1.cbor == f2.cbor
    assert QUERY.encode() not in f1.cbor and f1.body["query_sha256"]


def test_contested_items_are_labelled_carry_evidence_and_come_after_uncontested(rw, snap, provider, streams):
    disputed = _world(rw, provider, streams["a"])
    items = _recall(snap, provider, streams["a"]).body["items"]
    flags = [i["contested"] for i in items]
    assert flags == sorted(flags) and flags[-1] is True               # every uncontested item precedes it
    c = items[-1]
    assert c["object_id"] == str(disputed.object_id) and c["status"] == "contested"
    assert len(c["contradicting"]["event_ids"]) == 2 and c["contradicting"]["text"] == "never pin the payments image"
    assert all("contradicting" not in i for i in items[:-1])


def test_budgets_hold_and_a_tight_budget_drops_the_contested_item_first(rw, snap, provider, streams):
    _world(rw, provider, streams["a"])
    f = _recall(snap, provider, streams["a"], Budget(items=2, chars=4000))
    assert len(f.body["items"]) == 2 and not any(i["contested"] for i in f.body["items"])
    f2 = _recall(snap, provider, streams["a"], Budget(items=10, chars=40))
    assert sum(len(i["text"]) for i in f2.body["items"]) <= 40


def test_the_character_budget_counts_the_rendered_section(rw, snap, provider, streams):
    # D-0025 amendment 4 (owner, 2026-10-02; EXP-0004 E2): chars bound the RENDERED memory section.
    _world(rw, provider, streams["a"])
    full = _recall(snap, provider, streams["a"], Budget(items=10, chars=100_000))
    rendered = [len(render_memory_section({"coverage": "weak", "items": full.body["items"][:k]}))
                for k in range(len(full.body["items"]) + 1)]
    texts = [len(i["text"]) for i in full.body["items"]]
    cap = rendered[1] - 1                    # the first item's TEXT fits, but its rendered line does not
    assert texts[0] <= cap
    tight = _recall(snap, provider, streams["a"], Budget(items=10, chars=cap))
    assert len(render_memory_section(tight.body)) <= cap
    assert tight.body["items"][:1] != full.body["items"][:1]          # the first ranked item no longer fits
    assert tight.body["budget"] == {"items": 10, "chars": cap, "counts": "rendered"}
    for k, size in enumerate(rendered):                              # exact: k items fit when rendered[k] <= chars
        if k and size <= 100_000:
            f = _recall(snap, provider, streams["a"], Budget(items=k, chars=size))
            assert f.body["items"] == full.body["items"][:k] and len(render_memory_section(f.body)) == size


def test_frames_made_under_the_old_rule_rebuild_unchanged(rw, snap, provider, streams):
    _world(rw, provider, streams["a"])
    old = _recall(snap, provider, streams["a"], Budget(items=10, chars=60, counts="text"))
    assert old.body["budget"] == {"items": 10, "chars": 60}                       # no "counts": the old bytes
    assert sum(len(i["text"]) for i in old.body["items"]) <= 60
    assert Budget.of(old.body["budget"]) == Budget(10, 60, "text")
    assert Budget.of({"items": 3, "chars": 9, "counts": "rendered"}) == Budget(3, 9)
    with pytest.raises(ValueError):
        _recall(snap, provider, streams["a"], Budget(counts="tokens"))


def test_pruning_needs_two_active_channels_outside_their_top_m():
    def r(i, sem, lex, contested=False):
        from fractions import Fraction
        return Ranked(uuid.UUID(int=i), contested, 0, 1, sem, lex, 0, Fraction(0), 0)
    ranked = [r(1, 900, 9), r(2, 800, 8), r(3, 100, 1), r(4, 50, 0, contested=True)]
    both = {"semantic": True, "lexical": True, "entity": False}
    assert _pruned(ranked, both, 2) == {uuid.UUID(int=3)}             # outside top-2 in both; contested is pinned
    assert _pruned(ranked, {"semantic": True, "lexical": False, "entity": False}, 2) == set()
