"""Tests for eval/run_phase2_gate.py (EXP-0003 harness) and models/load_model_call_fixtures.py (recorded mode):
plumbing with a smart fake, the instrument pin, safety metrics, guard (ii), and recorded-mode determinism (gate 4)."""
import hashlib
import json
import uuid
from pathlib import Path

import pytest

from phase2_kit import SmartFake, world
from nacre.eval import run_phase2_gate as g
from nacre.models.load_model_call_fixtures import export_model_calls, load_model_calls
from nacre.models.recorded_provider import RecordedProvider

TASKS = Path(__file__).resolve().parents[2] / "tests" / "regression" / "mnexa" / "tasks"


def _family(n=14, i=0):
    return json.loads((TASKS / f"tasks_{n:03d}.json").read_text())["families"][i]


@pytest.fixture
def w(org, provider):
    return world(org, provider)


def test_the_instrument_is_mnexas_verbatim_and_pinned():
    assert hashlib.sha256(g.FIDELITY_INSTRUCTION.encode()).hexdigest() == g.INSTRUMENT_SHA256
    p = g.transfer_prompt("TASK", "")
    assert p.startswith("You are solving one operational task.") and "(none)" in p and p.endswith(g.FIDELITY_INSTRUCTION)
    assert g.TRANSFER_PARAMS.temperature is None and g.TRANSFER_PARAMS.max_tokens == 1024


@pytest.mark.parametrize("n", [14, 15, 16])
def test_a_family_runs_both_arms_with_zero_safety_violations(w, provider, n):
    fam = _family(n)
    stream = w["new_scope"]()
    fake = SmartFake()
    r = g.run_family(w["session"], provider, fake, fake, stream, fam, n)
    attempts = 3 if n == 16 else 1
    assert len(r.n_attempts) == len(r.c_attempts) == attempts
    assert r.memory and all("<<<" not in m for m in r.memory)
    assert r.n_pass and not r.c_pass                                  # echoed memory passes the grader; "(none)" does not
    assert r.safety == dict.fromkeys(g.SAFETY_METRICS, 0)
    assert r.sleep.episodes == 1 and fake.calls.count("eval.transfer.N") == attempts


def test_guard_ii_injected_challenges_never_reach_memory_and_unsafe_ones_are_refused(w, provider):
    fam = _family(14)
    stream = w["new_scope"]()
    r = g.run_family(w["session"], provider, SmartFake(), SmartFake(), stream, fam, 14)
    with w["session"]() as s:
        from nacre.ledger.read_stream import read_stream
        memory = " ".join(repr(e.body) for e in read_stream(s, provider, stream) if isinstance(e.body, dict)
                          and isinstance(e.body.get("content"), dict)
                          and e.body["content"].get("op") in ("lesson_proposed", "version"))
    for ch in fam["fallback_challenges"]["unsafe"]:
        assert ch["candidate"]["source_quote"] not in memory             # never in memory (the stream's capture
                                                                         # may hold it: it can be the failed decision)
    assert r.safety["unsafe_challenge_admitted"] == 0 and r.safety["injected_text_in_memory"] == 0


def test_metric5_counts_memory_text_the_model_never_produced(w, provider, monkeypatch):
    fam = _family(14)
    planted = fam["fallback_challenges"]["recoverable"][0]["candidate"]["source_quote"]
    r = g.FamilyResult("x")
    bodies_stream = w["new_scope"]()
    with w["session"]() as s:
        from nacre.eval.load_mnexa_family import load_mnexa_family
        from nacre.sleep.build_evidence_bundle import build_evidence_bundle
        from nacre.stores.propose_lesson import propose_lesson
        lf = load_mnexa_family(s, provider, bodies_stream, fam, 14)
        text = next(x.text for x in lf.sections if x.role == "correction")
        start = text.index(planted)
        propose_lesson(s, provider, stream_id=bodies_stream, decision_id=lf.decision_id, outcome_id=lf.outcome_id,
                       section_index=2, span=(start, start + len(planted)), nucleus=None)     # no model ever said it
        g._safety(s, provider, bodies_stream, fam, r, build_evidence_bundle(s, provider, bodies_stream, lf.outcome_id))
    assert r.safety["injected_text_in_memory"] == 1


def test_cross_scope_probe_sees_nothing(w, provider):
    stranger = uuid.uuid4()
    a = w["new_scope"](principal=stranger)
    b = w["new_scope"]()
    g.run_family(w["session"], provider, SmartFake(), SmartFake(), b, _family(14), 14)
    assert g.probe_cross_scope(w["open"], provider, stranger, b) == 0 and a


def test_gate4_a_recorded_replay_reproduces_every_grade_with_no_live_call(w, provider, tmp_path, no_network):
    fam = _family(16)
    live_stream = w["new_scope"]()
    live = g.run_family(w["session"], provider, SmartFake(), SmartFake(), live_stream, fam, 16)
    with w["session"]() as s:
        n = export_model_calls(s, provider, live_stream, tmp_path / "calls.jsonl")
    assert n == 2 + 6                                                  # propose + repair + 3 N + 3 C transfers
    replay_stream = w["new_scope"]()
    loaded = []

    def load(lf):
        with w["session"]() as s:
            loaded.append(load_model_calls(s, provider, replay_stream, tmp_path / "calls.jsonl",
                                           sources=(lf.decision_id, lf.outcome_id)))

    class Recorded:                                                    # RecordedProvider over the replay stream (lazy)
        name, replay, rp = "recorded", True, None

        def complete(self, request, *, timeout_s):
            if self.rp is None:
                with w["session"]() as s:
                    self.rp = RecordedProvider(s, provider, [replay_stream])
            return self.rp.complete(request, timeout_s=timeout_s)
    rec = Recorded()
    replay = g.run_family(w["session"], provider, rec, rec, replay_stream, fam, 16, before_sleep=load)
    assert loaded == [n]
    assert (replay.n_attempts, replay.c_attempts, replay.memory) == (live.n_attempts, live.c_attempts, live.memory)
    assert replay.sleep.calls_live == 0


def test_a_trial_passes_by_majority_of_its_attempts():
    r = g.FamilyResult("x", n_attempts=[True, False, False], c_attempts=[True, True, False])
    assert (r.n_pass, r.c_pass) == (False, True)
    assert g.FamilyResult("y", n_attempts=[True], c_attempts=[False]).n_pass


def test_metric3_counts_challenges_if_admission_ever_let_one_through(w, provider, monkeypatch):
    from nacre.sleep.admit_propositions import Admission, Admitted
    fam = _family(14)
    stream = w["new_scope"]()
    real = g.admit_propositions
    leaky = lambda bundle, props: Admission(structured=[Admitted(0, (0, 1), "x", "x", ())] * len(props))  # noqa: E731
    monkeypatch.setattr(g, "admit_propositions", lambda b, p: leaky(b, p) if p and p[0].quote.startswith(
        tuple(c["candidate"]["source_quote"] for c in fam["fallback_challenges"]["unsafe"])) else real(b, p))
    r = g.run_family(w["session"], provider, SmartFake(), SmartFake(), stream, fam, 14)
    assert r.safety["unsafe_challenge_admitted"] == len(fam["fallback_challenges"]["unsafe"])


def test_the_cross_scope_probe_does_see_memory_when_access_exists(w, provider):
    friend = uuid.uuid4()
    b = w["new_scope"](also=(friend,))                       # the owner runs it; friend IS granted b too
    g.run_family(w["session"], provider, SmartFake(), SmartFake(), b, _family(14), 14)
    assert g.probe_cross_scope(w["open"], provider, friend, b) >= 1
