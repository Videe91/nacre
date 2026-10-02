"""Tests for recall/narrow_by_identity.py (R14, D-0025 §3): conjunctive exact matching, relaxation most-specific-first
with every step recorded, the min_pool rule, exact-match counts for the entity channel, and address validation."""
import uuid

import pytest

from nacre.recall.narrow_by_identity import AddressError, narrow_by_identity


def _ids(n):
    return [uuid.UUID(int=i + 1) for i in range(n)]


def test_no_request_addresses_keeps_the_whole_pool():
    pool = _ids(3)
    r = narrow_by_identity(pool, {}, [])
    assert r.version_event_ids == tuple(pool) and r.relaxations == ()


def test_conjunctive_match_when_the_pool_is_large_enough():
    pool = _ids(40)
    addrs = {v: ["system:payments", "code:src/retry.py"] if i < 20 else ["system:payments"] for i, v in enumerate(pool)}
    r = narrow_by_identity(pool, addrs, ["system:payments", "code:src/retry.py"])
    assert r.version_event_ids == tuple(pool[:20]) and r.relaxations == ()
    assert r.exact_matches[pool[0]] == 2 and r.exact_matches[pool[30]] == 1


def test_relaxes_one_level_at_a_time_most_specific_first_and_records_it():
    pool = _ids(30)
    addrs = {v: ["system:payments"] + (["code:src/retry.py"] if i < 3 else []) for i, v in enumerate(pool)}
    r = narrow_by_identity(pool, addrs, ["code:src/retry.py", "system:payments"])
    assert r.relaxations == ("file+code",)                    # 3 < 16 with code; 30 with system only
    assert len(r.version_event_ids) == 30


def test_falls_back_to_global_when_nothing_matches_enough():
    pool = _ids(5)
    r = narrow_by_identity(pool, {pool[0]: ["system:payments"]}, ["system:payments", "entity:acme"])
    assert r.relaxations[-1] == "global" and r.version_event_ids == tuple(pool)


def test_matching_is_exact_on_type_and_value_with_types_case_insensitive():
    pool = _ids(1)
    r = narrow_by_identity(pool, {pool[0]: ["SYSTEM: payments "]}, ["system:payments"], min_pool=1)
    assert r.version_event_ids == tuple(pool)
    r2 = narrow_by_identity(pool, {pool[0]: ["system:payments-v2"]}, ["system:payments"], min_pool=1)
    assert r2.exact_matches[pool[0]] == 0 and r2.relaxations[-1] == "global"   # no prefix or fuzzy matching


@pytest.mark.parametrize("bad", ["payments", "planet:earth", "system:", ":x"])
def test_malformed_addresses_are_refused(bad):
    with pytest.raises(AddressError):
        narrow_by_identity(_ids(1), {}, [bad])
