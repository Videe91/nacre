# D-0016: Phase 2 regression suite, test modes and parity margins

- **Status:** accepted with owner amendments (2026-10-01). The framing is owner-decided (amendments below). The margin
  numbers are fixed in EXP-0001, committed before any run
- **Tier:** D3 (what counts as proof)
- **Date:** 2026-10-01
- **Relies on assumptions:** A-0003, A-0023, A-0024, A-0025

## Amendment history (owner, 2026-10-01)
1. **MNEXA is prior art, not proof.** Phase 2 must show two things:
   - **non-inferiority** to a **fresh MNEXA baseline** (MNEXA's own harness run live, k = 3, on the pinned model,
     **before any port**);
   - **superiority** over a **no-memory control** in the same runs.

   Both margins are fixed before any run. Safety metrics keep zero margin. No reruns to reach a pass. SPEC amended.
2. **Live parity scope:** the final pipeline only (014–016). Earlier seeds are covered by L1 and L2.
3. **Checker model:** deferred. It will be its own experiment with its own gate, and it ships only if it improves
   results (see D-0020).
4. **Model pinning:** always pin dated model versions. On a successor, re-baseline both MNEXA's harness and the
   no-memory control before any comparison.

**What this supersedes:** the original L3 below compared against MNEXA's historic single-run numbers (165/180). That
comparison is **superseded** by section B-L3 (amended). The original text is kept for history.

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

**L3 (AMENDED 2026-10-01): live non-inferiority and superiority.** This supersedes the original L3 below.
- **Arms:**
  - **B (fresh MNEXA baseline):** EXP-0001 runs MNEXA's own unmodified 014/015/016 harness. k = 3 full runs per
    set on `gpt-4o-mini-2024-07-18` (provider-default decoding), embedder pinned at a revision.
  - **C (no-memory control):** run in the same runs. An empty `MnexaSeed`, the same transfer reasoner and the set's
    own grader (MNEXA's seed-005 construction).
  - **N (Nacre):** later, the same design and the same model.
  - **Trials:** 3 sets × 20 families × 3 runs = 180 per arm.
- **Validity (checked on EXP-0001 itself):** B − C ≥ 30 pp. If not, the suite does not measure memory on this
  model, and the comparison stops.
- **Non-inferiority:** N ≥ B − 9 trials pooled (5 pp), and N_set ≥ B_set − 6 trials per set (10 pp).
- **Superiority:** N − C ≥ 54 trials pooled (30 pp), and N_set − C_set ≥ 9 trials per set (15 pp).
- **Safety:** Nacre has zero tolerance on every safety metric. MNEXA's safety numbers are reported, never used as an
  allowance.
- **Rules:**
  - No reruns to reach a pass.
  - An infrastructure abort is recorded, and the run is repeated in full with a new id.
- **Pre-registration:** `docs/experiments/EXP-0001-mnexa-rebaseline.md`, committed before the run.

**L3 (original proposal, superseded):** The gate only. Run by the owner locally with their own key (standing token rule).
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

## Owner answers (2026-10-01)
1. SPEC amended: MNEXA is prior art, not proof. Non-inferiority to a fresh baseline plus superiority over no memory.
2. L3 scope: 014–016 only.
3. Re-baselining approved. Dated pins always; on a successor, re-baseline MNEXA and the control first.
