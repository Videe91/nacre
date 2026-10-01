# EXP-0001: Fresh MNEXA baseline and no-memory control (Phase 2, pre-registered)

- **Status:** PRE-REGISTERED, 2026-10-01. This file is committed **before** any run. Its commit hash is the proof of
  order, and nothing below changes after a result is seen.
- **Decision it serves:** D-0016 (MNEXA is prior art, not proof).
- **Run by:** the owner, locally, with their own API key, using `scripts/run_mnexa_rebaseline.py`. The key is read
  from the environment and never written anywhere.

## Question
On the pinned model today, how often does MNEXA's own final consolidation pipeline (seeds 014–016) let the
transfer decision pass? How often does the same reasoner pass with **no memory**?

These two numbers are the fixed reference for Phase 2. Nacre must not be worse than the first by more than the
non-inferiority margin, and must beat the second by the superiority margin.

## Fixed setup
| Item | Value |
|---|---|
| Code | MNEXA at `2fc460c` plus its dirty working tree, exactly as frozen in `tests/regression/mnexa/MANIFEST.json`. It runs from a copy; `.env` is never copied. Every source file's sha256 is recorded in the run record |
| Task sets | `tasks_014.json`, `tasks_015.json`, `tasks_016.json`. Their sha256 must equal the manifest's, or the run refuses |
| Model | `gpt-4o-mini-2024-07-18` (a dated pin, owner rule). Decoding is provider default, exactly as MNEXA. Same model for every seat |
| Embedder | `sentence-transformers/all-MiniLM-L6-v2`, revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`, loaded offline |
| Harness | MNEXA's unmodified `experiments/seed_growth_014/015/016.py` `main()`. The runner only adds call logging around `OpenAIResponsesModel.generate`; prompts and outputs are unchanged |
| Replicates | k = 3 independent full runs per set (fresh state each run, including extraction and repair) |
| No-memory control | In the same runs, for every family: an **empty** `MnexaSeed`, then `decide(transfer prompt, entities, make_fidelity_reasoner(model))`, graded by the set's own `semantic_grade`. This is MNEXA's own seed-005 baseline construction. For 016 it is 3 attempts per family per run with majority grading, mirroring 016's condition |
| Logging | Every call is written to the run directory as `{prompt, prompt_sha256, response text, response_id, model reported, usage}`. These are MNEXA's first recorded prompts |

## Metrics
A **trial** is (set, family, run). There are 3 sets × 20 families × 3 runs = **180 trials per arm**.

- **B (fresh MNEXA baseline):** the trial passes if the family passes in MNEXA's final-pipeline condition:
  - 014 `lossless`;
  - 015 `verbose`;
  - 016 `verbose` majority (MNEXA's own definition).
- **C (no-memory control):** the trial passes if the control decision passes `semantic_grade`.
- **Safety (reported per arm):**
  - unsupported claims admitted;
  - unsafe, fabricated and non-authoritative support admitted;
  - grounded-fallback ancestry validity.
- **Also reported:** per-set counts, per-run counts, tokens and calls, and wall time.

## Margins, fixed now
These apply later to Nacre's run (N, the same design and model).

- **Validity of the suite (checked on this run):** B − C ≥ **30 percentage points** pooled. If not, the tasks do
  not measure memory on this model. The comparison stops and goes back to the owner.
- **Non-inferiority:**
  - pooled: N ≥ B − **9 trials** (5 pp of 180);
  - each set: N_set ≥ B_set − **6 trials** (10 pp of 60).
  - How strict this is depends on the baseline level:
    - with both arms at a true rate of 0.95, the pooled false-fail rate is about 1.5%;
    - at 0.85 it is about 9%.

    Both figures ignore family clustering. This is stated now, not argued later.
- **Superiority over no memory:**
  - pooled: N − C ≥ **54 trials** (30 pp);
  - each set: N_set − C_set ≥ **9 trials** (15 pp).
- **Safety:** Nacre has zero tolerance on every safety metric. MNEXA's safety numbers in this run are reported, and
  are never used as an allowance.

## Stopping and rerun rules
- **The run happens once.** All 9 harness runs plus all control trials complete, or the run is incomplete.
- **Infrastructure failure** (network, rate limit, crash): keep the partial record, mark it `aborted`, and start a
  complete new run with a new run id. Both are reported. A run is never discarded because of its **results**.
- **Model unavailable** (A-0024): stop. Apply the successor route: re-baseline both MNEXA and the control on a dated
  successor, after owner approval.

## Run log
- **`EXP-0001-20261001T061800Z-8eafe9`: ABORTED (infrastructure).**
  - The runner process ended after 4 calls (first family of 014 rep 1, 06:18 UTC) and recorded no reason.
  - The 4 calls confirm that `gpt-4o-mini-2024-07-18` is served under that exact name (A-0024).
  - Not a result. It is not summarised, and its outputs are not used.
  - Afterwards the runner was changed: step output now streams to disk, and SIGINT/SIGTERM/SIGHUP mark a run
    `aborted` with the reason. The design is unchanged.

## Results (run `EXP-0001-20261001T071654Z-69b7b4`, complete; frozen in `tests/regression/exp0001/`)

**Run facts:**
- 18/18 steps, all exit 0.
- Every one of the 1,260 calls reported `gpt-4o-mini-2024-07-18`.
- 811,078 input and 105,426 output tokens.
- The pre-registration commit `5eb30b3` precedes the run (runner at `1ddd3a4`). The margins were not changed.

| Set | B (fresh MNEXA) | C (no memory) | Per run (B, C) |
|---|---|---|---|
| 014 | 59/60 | 8/60 | (20,3) (19,3) (20,2) |
| 015 | 59/60 | 6/60 | (19,2) (20,2) (20,2) |
| 016 | 60/60 | 7/60 | (20,3) (20,2) (20,2) |
| **Pooled** | **178/180** | **21/180** | |

- **Validity:** B − C = 87.2 pp ≥ 30 pp. **PASS** (A-0029 validated).
- **MNEXA safety totals:** all 0 (unsupported, unsafe, fabricated and non-authoritative admissions; invalid fallback
  ancestry).

**Margins for Nacre, now frozen** (owner, 2026-10-01):

| Check | Pooled | Per set (014 / 015 / 016) |
|---|---|---|
| Non-inferiority | N ≥ **169** | ≥ **53 / 53 / 54** |
| Superiority | N ≥ **75** (C + 54) | ≥ **17 / 15 / 16** (C_set + 9) |
| Safety | **zero** on every metric | |

**The superiority bound is now implied by non-inferiority** (169 ≥ 75; 53/53/54 ≥ 17/15/16). Both checks are
kept anyway (owner).

**Note on the no-memory arm:** it passes 21/180 (11.7%). Some transfer tasks are partly solvable from the task text
alone.

**Attribution finding (post-run analysis; does not change the frozen numbers):**
- **The injection:** MNEXA's 014–016 harness injects task-author "recoverable" challenge candidates into the proposal
  list before admission (`ensure_fallback_challenges`). In all 60 families, that candidate contains the answer
  clause.
- **Where the clause came from:**
  - in 147/180 trials it entered memory through **model-proposed structured** propositions, which injected
    candidates cannot become by design;
  - in the other 33 it entered through fallback records, and in **all 33** the model's own extraction or repair
    output in the **same run** contained the clause (matched by call time window and source text).
- **Reading:** strong evidence that B did not depend on injected content. It is not proof, because both copies are the
  same source span and the record does not say which one was admitted. See `docs/plans/phase-2-shortcut-audit.md`.
