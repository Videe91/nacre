# D-0019: Write-gate scoring (surprise, stakes, direct statements)

- **Status:** accepted (owner, 2026-10-01) with revisions R1–R4 (R4 refines R1's failing-evaluation clause)
- **Tier:** D2 (cross-module behaviour)
- **Date:** 2026-10-01
- **Relies on assumptions:** A-0028

## Context
- **SPEC:** the write gate scores events on surprise (predicted vs actual), stakes (money, clients, production,
  irreversible) and direct statements from people. The learning mode shifts the threshold. Events above it are
  **flagged**; nothing is promoted by the gate. The sleep pass runs only where flagged events exist.
- **MNEXA has no such gate** (inventory §0.6). MNEXA consolidated every decision that had an outcome, and promotion
  was a quorum. So this is **new work with no MNEXA evidence**. It must not reduce what the ported pipeline learns:
  if the gate fails to flag an episode, the lesson is never proposed.

## Options considered
1. **Flag everything that has an outcome.** This is MNEXA's effective behaviour.
   - Pros: exact parity.
   - Cons: no gate. Sleep-pass cost grows with every event, which is the SPEC risk "sleep and tuner cost".
2. **Model-scored salience.**
   - Pros: nuanced.
   - Cons: a model call per event, non-deterministic, and an injection surface (untrusted text could argue its way
     in).
3. **Deterministic rules plus a non-regression constraint (recommended).**

## Decision (proposed)
Option 3.
- **Score:** `score = max(surprise, stakes, statement)`, each in [0, 1].
- **Surprise:**
  - 1.0 when an outcome has `success = false` and there is either no prediction or a prediction that did not
    expect failure;
  - 1.0 when `evaluates_prediction` links to a prediction whose `expected_outcome` differs from the observed
    `success`;
  - 0.5 when `success = null` and the outcome has an authoritative `correction` section (D-0018);
  - otherwise 0.
- **Stakes:**
  - 1.0 if the decision, action or outcome carries a `stakes` tag (`money | client | production | irreversible`),
    set by the agent or by config rules;
  - 0 otherwise.
  - Config rules are a `config_event` of versioned keyword → tag rules, recorded in the ledger. There is no keyword
    matching on untrusted text unless a rule opts in.
- **Statement:** 1.0 for a trusted `statement` or `correction` from `actor_kind = person`; 0 otherwise.
- **Threshold by mode** (`mode` on the envelope; from config, versioned):
  - `normal` 0.5
  - `incident` 0.0 (flag everything; the sleep pass runs at once)
  - `exploration` 0.5
  - `onboarding` 0.5

  These are placeholders, tuned in Phase 4.
- **Output:** `memory_event` op `flag` with `{target_event_id, score, components, threshold, mode, config_version}`.
  - It is appended once per target (idempotency key = target id plus config version).
  - It is scored when the outcome arrives, the point at which the episode can be judged.
- **Non-regression constraint (gate item):** on every frozen regression set, the gate must flag **100%** of the
  lesson-bearing episodes. In MNEXA those are the failed decision → authoritative correction pairs. Measured
  deterministically, 0 model calls.

## Revision after EXP-0001 (2026-10-01, proposed; owner to approve)
- **R1. An authoritative correction always flags.**
  - Any outcome carrying an **authoritative** `correction` or failing-`evaluation` section (D-0018 authority) scores
    **1.0**, whatever the prediction said.
  - Why: in EXP-0001 every piece of lesson content in B came from authoritative-correction spans. Under the original
    surprise rule, an agent that *predicted* failure would get surprise 0. The episode would not be flagged, and the
    correction, the most valuable signal, would never be consolidated.
- **R2. The non-regression check uses the real capture mapping.** Flag recall is measured on 003–016 families
  converted through the same harness mapping as R3 of D-0020 (decision → outcome with sections and envelope
  authority), not on a separate idealised encoding.
- Nothing in EXP-0001 bears on stakes or statement scoring; those stay as proposed.

## Revision R3 (owner, 2026-10-01): selectivity, pre-registered before measuring
- **Owner requirement:** alongside 100% recall on lesson-bearing episodes, gate item 7 measures **selectivity** on a
  separate set of routine episodes. A gate that flags everything must fail item 7.
- **Frozen set:** `tests/regression/routine/routine_episodes_v1.json`, sha256
  `cad974a7dc541cb496f33918520835010d7b349b71562f5e54b36679c751e8f6`.
  - 200 episodes from `scripts/make_routine_episodes.py` (seed 20261001), its own vocabulary, not drawn from
    003–016.
  - Every episode: the prediction was correct, the outcome was as expected, no correction section, no stakes tag.
  - 50 are expected failures (for example, a new regression test failing before the fix).
  - The observed result is always an `evaluation` section, as a real CI report would be.
  - Sources: ci / review (trusted integration results) and tool (untrusted).
- **Ceiling (proposed by Claude before any measurement):** the normal-mode flag rate on the routine set must be
  **≤ 5% (≤ 10 of 200)**.
- **Canary:** with the normal threshold set to 0 (a gate that flags everything), item 7 must fail.
- **Disclosed before measuring:** counting the frozen set shows **32 episodes are trusted expected failures**. As
  written, R1 ("a failing evaluation always flags, whatever the prediction") flags them, so the gate is **expected to
  fail the ceiling (about 16%)**. That is a finding about R1, not a reason to change the set or the ceiling. The
  measurement is run as registered and reported to the owner.

### R3 measured (2026-10-01, as registered)
- **Result:** **32 / 200 flagged = 160 per mille (16%) > ceiling 50.** **Item 7 FAILS on selectivity.** Recall stays
  at 100% (A-0028).
- **Which episodes:** exactly the 32 trusted (ci / review) expected failures. R1's clause "a failing evaluation always
  scores 1000, whatever the prediction" flags them. No other routine episode is flagged.
- **Canary:** threshold 0 flags 200/200, and item 7 fails, as required.
- Recorded in `tests/eval/test_measure_gate_selectivity.py`; item 7 is held open by a strict xfail.

**Proposed fix (R4, needs owner approval, because R1 was owner-approved):**
- An authoritative **correction** section always flags, as now.
- A **failing evaluation** keeps its D-0018 authority (a span inside it can ground a lesson) but **flags only by the
  surprise rule**: when the failure was not predicted.
- **Predicted effect, computed and not yet measured:**
  - routine set: 0 / 200 flagged;
  - recall stays 100% (005–016 carry correction sections; 003/004 are unpredicted failures).

  If approved, R4 is measured against the same frozen set and ceiling.

## Revision R4 (owner, 2026-10-01): predicted failures, with anti-gaming rules
Approved after the R3 measurement (32/200 routine expected failures flagged by R1's failing-evaluation clause).

- **An authoritative `correction` always flags** (unchanged from R1).
- **A failing outcome is NOT flagged only when a valid prediction foresaw exactly that failure.** A failing
  evaluation keeps its D-0018 authority (a span inside it can still ground a lesson), but it no longer flags by
  itself. A prediction is valid only if all of these hold:
  1. it `evaluates_prediction`-links to the outcome and expected failure (`expected_success = false`);
  2. it **names the specific failing test or check** (`expected_failing_check`, D-0018 amendment 1). A vague
     prediction with no named check never suppresses;
  3. it **precedes the action in commit order**: committed before the first `action` executing the same decision,
     or, when the episode records no action, before the outcome;
  4. the outcome's recorded `failing_checks` are **exactly** `{expected_failing_check}`, after trimming and
     casefolding. A different failure, an extra failure, or an outcome that names no failing check all flag.
- Otherwise the surprise rules stand: an unpredicted failure flags, and a success where failure was predicted flags.
- **Confirmation (owner):**
  - a second routine set built **in a separate session, frozen before measuring**, flag rate ≤ 5%;
  - adversarial cases that must ALL flag: a vague prediction, a mismatched failure, a prediction recorded after the
    action;
  - recall on 003–016 stays 100%.

### R4 confirmation set, FROZEN before measuring (2026-10-01)
- **File:** `tests/regression/routine/routine_episodes_v2.json`, sha256
  `b1900a51f54f9797f94bb1d61f9c62190d7ae9196da68d23694d44d2a1361aad`. Built by a SEPARATE session blind to the gate
  code (generator `scripts/make_routine_episodes_v2.py`, seed 730214).
- **Contents:** 200 routine episodes (140 expected successes; 60 expected failures named by check, predicted before
  the action, failing exactly that check) plus 75 adversarial episodes (25 vague, 25 mismatched, 25 late).
- **Verified by this session before freezing (structure only, no measurement):** shape, ordering, naming, roles,
  sources; no names reserved for other sets; byte-identical regeneration; secret scan clean.
- **Bar:** routine flag rate ≤ 5% (≤ 10/200), and ALL 75 adversarial episodes flagged, and recall on 003–016 = 100%.

## Why this one
- It is deterministic, costs nothing, and cannot be argued with by untrusted text.
- It covers the SPEC signals.
- It is provably no worse than MNEXA on MNEXA's own tasks, which is the one thing measurable now.

## Consequences
- **To build:** `gate/score_event.py` and `gate/flag_events.py`.
- **Stakes tagging:** capture accepts `stakes` tags.
- **Not measured yet:** gate precision (how much is flagged in real use). Real workloads come in Phase 5 tracks;
  thresholds are tuned in Phase 4.

## How we'd know it was wrong
- A regression-set episode is not flagged (the gate fails).
- Later: real incidents missed, or the sleep-pass cost is not reduced.

## Questions for the owner
1. Should Phase 2 ship deterministic rules only, with any model-scored salience a later ADR?
2. Approve the placeholder thresholds, with `incident` = flag everything?
