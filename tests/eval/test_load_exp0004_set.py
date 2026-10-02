"""Tests for eval/load_exp0004_set.py: the frozen sha256 pins (equal to the EXP-0004 table), and the arm view carrying no
grading field. Uses ONLY the dev split and synthetic data; the sealed test split is never opened here."""
import hashlib
import json
import re
from pathlib import Path

import pytest

from exp0004_kit import tiny_split
from nacre.eval import load_exp0004_set as L

ROOT = Path(__file__).resolve().parents[2]
SET_DIR = ROOT / "tests" / "regression" / "exp0004"
DOC = (ROOT / "docs" / "experiments" / "EXP-0004-recall-under-interference.md").read_text()
_GRADING_KEYS = ("grading_refs", "expected_ask", "erasure_target", "holds_twin_facts_for", "twin_scopes", "type",
                 *L.REGEX_FIELDS)


def test_the_sha256_pins_equal_the_frozen_set_table():
    assert re.search(r"`tests/regression/exp0004/test\.json` \(sealed;[^|]*\| `" + L.TEST_SHA256 + "`", DOC)
    assert re.search(r"`tests/regression/exp0004/dev\.json`[^|]*\| `" + L.DEV_SHA256 + "`", DOC)


def test_the_dev_file_on_disk_matches_its_pin():                    # the sealed test file is never opened in a test
    assert hashlib.sha256((SET_DIR / "dev.json").read_bytes()).hexdigest() == L.DEV_SHA256


def test_a_file_that_does_not_match_its_pin_is_refused(tmp_path):
    p = tmp_path / "dev.json"
    p.write_bytes((SET_DIR / "dev.json").read_bytes() + b" ")
    with pytest.raises(L.SetIntegrityError):
        L.load_split(p, L.DEV_SHA256)


def _assert_no_grading_in(scopes, grading):
    blob = json.dumps([{"scope": s.scope_id, "principal": s.principal, "granted": s.granted_scopes,
                        "persons": s.persons, "erase": s.erase_persons, "days": s.days,
                        "tasks": [t.__dict__ for t in s.tasks]} for s in scopes])
    for key in _GRADING_KEYS:
        assert f'"{key}"' not in blob
    for g in grading.values():
        for pattern in g.regexes.values():
            assert pattern is None or pattern not in blob
    for t in (t for s in scopes for t in s.tasks):
        assert set(t.__dict__) == {"task_id", "prompt", "addresses"}


def test_the_dev_split_loads_into_a_leak_free_arm_view():
    scopes, grading = L.load_split(SET_DIR / "dev.json", L.DEV_SHA256)
    assert len(scopes) == 15 and len(grading) == 60
    assert {s.set_name for s in scopes} == {"S1", "S2", "S3"}
    cats = [g.category for g in grading.values()]
    assert cats.count("E") == 6 and cats.count("T3") == 15 and cats.count("T5") == 15
    assert all(g.expected_ask == (g.category in ("T5", "E")) for g in grading.values())
    _assert_no_grading_in(scopes, grading)


def test_an_unknown_new_grading_field_never_reaches_the_arm_view():
    data = tiny_split()
    data["scopes"][0]["tasks"][0]["secret_grader_hint"] = "the answer is Lisbon"
    data["scopes"][0]["days"][0]["episodes"][0]["events"][0]["grader_note"] = "target"
    data["scopes"][0]["days"][0]["episodes"][0]["grading_refs"] = {"x": 1}
    scopes, grading = L.split_views(data)
    blob = json.dumps([[s.days, [t.__dict__ for t in s.tasks]] for s in scopes])
    assert "secret_grader_hint" not in blob and "grader_note" not in blob and "grading_refs" not in blob
    _assert_no_grading_in(scopes, grading)


def test_days_are_in_day_order_and_grading_keeps_the_refs():
    data = tiny_split()
    data["scopes"][0]["days"].reverse()
    scopes, grading = L.split_views(data)
    assert scopes[0].days[0][0][0]["event_id"].endswith(":0001")
    assert grading["x-s1-01:task1"].refs["erased_person"] == "x-s1-01-p1"
    assert grading["x-s1-01:task1"].category == "E" and grading["x-s1-01:task3"].category == "T3"
