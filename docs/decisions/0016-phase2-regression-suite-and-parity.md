# D-0016: Phase 2 regression suite, test modes and parity margins

- **Status:** proposed
- **Tier:** D3 (what counts as proof)
- **Date:** 2026-10-01
- **Relies on assumptions:** A-0003, A-0023, A-0024, A-0025

## Context
SPEC ("Proof", tier 1) says MNEXA's frozen task sets must reproduce "at the same or better rates" on the new build,
and that this is the gate for porting. Reading MNEXA (`docs/plans/phase-2-mnexa-port-inventory.md` §0) shows:
- Every result is a **single live run** (n = 20, `gpt-4o-mini`, provider-default decoding). Prompts were not
  recorded.
- The substrate code the runs used is not in MNEXA's git history.
- MNEXA's raw results existed only on one machine. They are now frozen in `tests/regression/mnexa/`.

The owner requires two test modes:
- **recorded** model responses, stored as ledger events, for everyday tests;
- **live** repeated runs for the gate.

Parity margins must be fixed **before any run**.

## Options considered
1. **Literal "same or better".** Nacre's single live run ≥ MNEXA's single run, per set.
   - Cons: it compares two single samples. The false-fail rate is high even for a perfect port (P(20/20) at a true
     rate of 0.97 is 0.54), so the gate would be decided by chance.
2. **End-to-end live replicates only.** k runs per set, compared with MNEXA within margins.
   - Pros: statistically sound.
   - Cons: costs model calls for every check; cannot tell port drift from model variance; nothing runs every day.
3. **Layered parity (recommended).** Layer on top of each other:
   - deterministic, zero-margin checks that reuse MNEXA's own stored outputs;
   - a small live-replicate check within pre-set margins;
   - recorded replays for everyday tests.

## Decision (proposed)
Option 3.

### A. The frozen suite
- **Contents:** `tests/regression/mnexa/` holds byte copies plus `MANIFEST.json` (sha256 per file, and MNEXA HEAD
  `2fc460c` with its dirty state).
- **Changes:** any change to a frozen file is a new, named version with an ADR. The originals are never edited.
- **Integrity test (gate item 1):** every copied file matches its manifest hash. When `~/Desktop/mnexa` is present,
  the hash-only entries are checked too (task sets 017–029, results 030–035, and the SQLite states).
- **Exposure:** under MNEXA ADR-0004 these sets are EXPOSED. They are valid for regression, **never** for new
  confirmatory claims.

### B. Levels

**L1: grader parity.** Deterministic, 0 model calls, zero margin, every run.
- Nacre's port of MNEXA `grade_text` (and the diagnostic regrade profiles) grades the decisions stored in each frozen
  `result.json`.
- It must reproduce MNEXA's recorded per-family grades **exactly**.
- Sets: 003–016 (001/002 only as diagnostics; their graders changed after the outputs were seen).

**L2: mechanism parity.** Deterministic, 0 model calls, zero margin, every run.
- MNEXA's stored raw model responses for 007–016 are fed to Nacre's gates in place of a model:
  - `raw_atom_model_response` (007–009),
  - `raw_boundary_model_response` (010),
  - `initial_raw_model_response` (011–016),
  - `repair.raw_model_response` (012–016).
- Every admission, rejection (with its reason) and fallback record must equal MNEXA's recorded ones, byte for byte
  after canonical JSON.
- This is the port-drift check: same model output in, same memory out.

**L3: live parity.** The gate only. Run by the owner locally with their own key (standing token rule).
- **What runs:** the Nacre pipeline end to end (capture → sleep pass → admitted memory → transfer decision graded by
  L1's grader).
- **Sets and conditions:** the final ported pipeline on the verbose lossless-memory condition of sets 014 (B), 015
  (A) and 016 (A). MNEXA measured 20/20, 20/20 and 60/60 on these.
- **Replicates:** k = 3 per family, so 180 trials in total. Each replicate uses fresh, isolated state.
- **Model:** `gpt-4o-mini` with provider-default decoding, exactly MNEXA's configuration (MNEXA ADR-0003 compute
  parity). The consolidation and reasoning seats use the same model.
- **If that model is unavailable (A-0024):** re-baseline. Run MNEXA's own frozen harness with the successor model,
  k = 3, **before** any Nacre live run, and substitute those numbers for MNEXA's. Owner approval is required.
- **Margins, fixed now:**
  - Pooled pass count ≥ **165 / 180**, and each set ≥ **52 / 60**.
    - If the true pass rate is 0.95, this falsely fails about 3% of the time.
    - A drop to 0.90 is caught with probability 0.73.
    - The calculation is in the planning notes. It rests on MNEXA's 60/60, since P(60/60) at a true rate of 0.90 is
      0.002.
  - **Zero-margin safety metrics:**
    - unsupported or unsafe admissions = 0;
    - adversarial, role and fallback challenges rejected = 100%;
    - no invented span admitted.
- **No reruns to reach a pass.** A failed L3 is a finding. The investigation is recorded; the run is not
  re-rolled.

**Belief lifecycle.** Deterministic.
- MNEXA's 48 tests (ledger 34–39) are translated to the Nacre API and must pass.
- Deviation tests (D-0017) show the four MNEXA bugs are fixed.

### C. Test modes
- **Recorded mode (everyday):**
  - Every model call is a `model_call` ledger event (D-0022).
  - `RecordedProvider` answers a request **only** from a recorded event with the same canonical request hash.
  - A miss is a hard error. Recorded mode never calls a live model, and the network is blocked in tests.
  - Recordings come from:
    - (i) MNEXA's stored outputs, for L1 and L2;
    - (ii) Nacre's own L3 gate run, exported as fixtures. Replaying it must reproduce every grade exactly.
- **Live mode (gate):** `LiveProvider` against the pinned model. Calls are recorded as they happen, so every live
  run becomes a replayable recording.

## Why this one
- L1 and L2 separate port drift from model variance at zero cost and with zero tolerance.
- L3 is sized so that a correct port passes and a real regression fails.
- Recorded mode keeps the everyday suite fast and deterministic.
- Nothing about the margins can be tuned after seeing results.

## Consequences
- Needs the evaluation harness (`src/nacre/eval/`), the provider interface (D-0021) and recording (D-0022).
- L3 costs about 180 transfer calls plus the consolidation calls (small on `gpt-4o-mini`), run by the owner.
- **SPEC wording conflict:** "same or better rates" must be read statistically, as defined here. This needs owner
  approval and a SPEC amendment.

## How we'd know it was wrong
- L1 or L2 cannot be made exact because MNEXA's stored outputs are incomplete. That would be a finding, recorded
  per set.
- L3 passes while a known regression was deliberately introduced. Canary: running L3 with the role gate disabled
  must fail.

## Questions for the owner
1. Approve reading "same or better" as the L3 margins above, and amending SPEC to match?
2. Should L3 cover only the final verbose pipeline (014, 015 and 016), with the earlier variants covered by L1 and
   L2 only?
3. If `gpt-4o-mini` is retired, approve the re-baseline route (MNEXA harness plus successor model, run first)?
