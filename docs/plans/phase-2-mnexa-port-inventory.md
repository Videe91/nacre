# Phase 2: MNEXA port inventory (PROPOSED, for owner approval)

- **Source:** `~/Desktop/mnexa` at `2fc460c` (branch `spec/mnexa-v0`), read 2026-10-01.
- **Working tree at read time:** dirty. `mnexa_seed.py` is modified (+498/−7). The capability and retrieval-index
  modules and 27 test files are untracked.
- **How it was read:** file reads by three read-only agents, then spot-checked by hand. The spot-checks were git
  history, task hashes, gitignore, and whether `success` is read. No MNEXA code was run and no model was called.

## 0. Facts that change the port

Evidence for each fact is in brackets.

1. **The consolidation mechanisms exist only in experiment scripts.**
   - This covers seed growth 002–016.
   - Production `MnexaSeed.consolidate()` (`mnexa_seed.py:4119-4209`) stores whatever string a builder returns
     as one `belief`.
   - Span grounding, role gate, atomicity, structured propositions, repair, fallback and compaction live in
     `experiments/seed_growth_0NN.py`.
   - So "porting" them means turning experiment code into production functionalities. Only the regression suite
     can catch drift.
   - [grep of the production modules; reader report]
2. **The experiments ran against substrate code that was not in git at the time.**
   - Seed 005 was frozen in `7f30caf` on 2026-09-13.
   - `mnexa_seed.py` first enters git in `2b73c76` on 2026-09-14, after seed 029.
   - The runs cannot be reproduced from MNEXA's git history. [`git log --diff-filter=A`, verified]
3. **The raw results were unversioned.**
   - `experiments/results/` is in MNEXA's `.gitignore` (91 MB, this machine only).
   - **Frozen into Nacre on 2026-10-01** (see §3). [`git check-ignore`, verified]
4. **Every seed run is one live run.**
   - n = 20 families, model `gpt-4o-mini`, provider-default decoding.
   - No temperature or seed is set anywhere. Prompts, prompt hashes and response ids were not recorded.
   - The only replication is seed 016 (3 replicates).
   - Grading is deterministic regex; there is no LLM judge.
   - [third reader; `seed_growth.py:71`]
5. **Several mechanisms leaned on oracles that real data does not have.**
   - 006 used hand-authored atoms.
   - 008 used roles pre-marked in the source text (`<<<ROLE:…>>>`).
   - 009 used pre-registered canonical boundaries.
   - Removing the oracle (010, autonomous boundaries) was a **negative result**: 18 → 14.
6. **The write gate has no scoring in MNEXA.**
   - Promotion = a quorum of ≥ 2 distinct decisions with the same normalised lesson text, plus an ancestry check.
   - No surprise, stakes or salience exists in any `.py` file.
   - `success` is written but **never read**, so a failed and a successful outcome count the same.
   - Nacre's surprise and stakes gate is therefore **new work, not a port**. [grep, verified]
7. **Episodes have no implementation.** ADR-0009 is accepted, and its code status reads "Pending. No
   implementation exists." In the code, an "episode" is just a decision → outcome pair.
8. **The belief lifecycle is production code, with no live evidence.**
   - It covers propose, quorum promote, version, contradict, contest and supersede.
   - It is checked by 48 deterministic unit tests. It never ran on a task set with a live model.
   - The one exception: seed 030 exercised promotion, with the lesson text forced to a canonical string.
9. **MNEXA's `docs/state/CURRENT.md` is stale.**
   - It says "specification phase, no production code".
   - The ledger reaches entry 41 and reports 536/536 tests passing.
   - `tests/test_capability_runtime.py` cannot even be collected: it imports a module that does not exist.

## 1. Mechanisms to port, with evidence

Legend:
- **P** = production code
- **X** = experiment-only
- **A** = ADR only
- **live** = measured with a live model
- **det** = deterministic tests only

### Capture

| Mechanism | What it does | Where | Evidence | Status |
|---|---|---|---|---|
| Decision and outcome evidence | `DecisionMade` (refs the context it came from) and `OutcomeObserved` (`refs=(decision_id,)`, `metadata={success, outcome_for}`). A missing outcome means no evidence, never failure | P `mnexa_seed.py:2034-2214` | ADR-0016 (rules 1–23, X-23…X-30); commits `90e01fe`, `277d03f`; det | Partial: `PredictionMade`, `ActionExecuted`, `execution_of` and `evaluates_prediction` are **not implemented**. Model attribution, `committed_by` and rule 2a are missing |
| External decision ingestion | `prepare_context` freezes a `ContextFrame` with an evidence hash. `record_external_decision` verifies the frame hash, then writes `DecisionMade{reasoning_owner:"external"}` | P `mnexa_seed.py:1544, 1805-2028`; `mnexa_context.py:127-407` | ledger 34; 7 tests; `9d66e9f`; det | Needs recall (Phase 3). Phase 2 records decisions with `decided_from` optional |
| Idempotent writes | Key plus canonical request hash; conflict raises an error; atomic | P `mnexa_seed.py:96-656` | ADR-0018; ledger 35; 9 tests; `78f2129`; det | **Already exceeded by Nacre Phase 1** (D-0012 principal-bound request MAC) |

### Write gate

| Mechanism | What it does | Where | Evidence | Status |
|---|---|---|---|---|
| Two-stage proposal → promotion | `propose_lesson` needs ≥ 1 observed outcome. It feeds `"DECISION…\nOUTCOME…"` to the builder and writes `LessonProposed{authority:"proposal_only"}`, which is not searchable | P `mnexa_seed.py:2220-2424` | ledger 36; 7 tests; `b20338a`; det | Port. Gap: no idempotency key, so duplicates are collapsed later |
| Evidence-gated promotion and versioning | Normalise with casefold and whitespace. Need ≥ 2 distinct decisions with valid decision → outcome ancestry and the **same** normalised text. `i_auto_<sha>` identity. New independent support creates a new version, keeping the text | P `mnexa_seed.py:2431-2838` | ledger 37; 8 tests; `591a56c`, `1e1baaa`; det | Port. Quorum is exact-text only (A-0027) |
| Surprise, stakes and direct-statement scoring | — | none | none (SPEC design) | **New.** Proposed D-0019 |

### Sleep pass (consolidation lineage; all X, all live gpt-4o-mini, n = 20)

Each set is also listed in the manifest described in §3.

| Seed | Mechanism | Code | Ledger / commit | Measured (conditions) | Port? |
|---|---|---|---|---|---|
| 002 | Lossless consolidation prompt | `seed_growth.py:149` | 04, 05 / `4464d98` | 1/5 → 3/5; not frozen; grader changed after seeing outputs (002R) | Superseded by 005 |
| 004 | Decision fidelity instruction (reasoning seat) | `seed_growth_004.py:30` | 07 / `395cd5b` | A 0 / B 17 / C 19 | Reasoning-seat prompt → Phase 3 |
| 005 | Evidence-disciplined consolidation: only the authoritative correction grounds a lesson | `seed_growth_005.py:34,109,138` | 08 / `7f30caf` | contamination 12 → 0; transfer 17 → 20 | **Port** |
| 006 | Claim ancestry, closed-world admission | `seed_growth_006.py:200,447` | 09 / `5f5a587` | 80/80 admitted, 0 unsupported; 17 = 17; **oracle atoms** | Port the admission rule, not the oracle |
| 007 | Raw-span grounding (exact unique quote) | `seed_growth_007.py:44,107` | 10 / `47cc045` | 40/40 adversarial rejected; 15 = 15. Note: `ungrounded_atoms_admitted` is a **hard-coded 0** | **Port**; measure that metric for real |
| 008 | Authoritative role gate | `seed_growth_008.py:12-28,101,377,471` | 11 / `b458c9f` | transfer 9 → 18; precision 0.425 → 0.85; **roles pre-marked in text** | **Port.** Roles come from structured outcome fields plus envelope trust (D-0018, A-0026) |
| 009 | Atomicity gate | `seed_growth_009.py:79,269` | 12 / `e6c464f` | precision 0.667 → 1.0; 19 = 19; **oracle boundaries** | Superseded by 011/012 (no oracle) |
| 010 | Autonomous boundary discovery | `seed_growth_010.py:46,230,457` | 13 / `1973676` | **negative:** oracle 18 vs autonomous 14 | Keep as a negative result; not ported alone |
| 011 | Structured propositions (nucleus plus qualifiers, grounded) | `seed_growth_011.py:55,63,371,776` | 14 / `07bfe1f` | nucleus recall 0.35 → 0.575; transfer 13 → 14; **complete lessons 15 → 11** | **Port** |
| 012 | Structure repair (second model pass) | `seed_growth_012.py:52,60,328` | 15 / `4e4c377` | precision 0.69 → 0.97; **transfer 12 → 10; 1 invented nucleus admitted** | **Port, with a flag** (see D-0020 question 2) |
| 013 | Semantic closure: nucleus = retrieval handle, support span = payload | `seed_growth_013.py:120,178` | 16 / `c4c5568` | 15 → 16; complete lessons 9 → 15; words 39.7 → 146 | **Port** (record shape); rendering → Phase 3 |
| 014 | Support-first lossless fallback (`structure_status:"unresolved"`) | `seed_growth_014.py:68,235,521,582` | 17 / `c6bdde3` | 15 → **20**; violations 5 → 0; 0/40 unsafe | **Port** |
| 015 | Compact evidence index (dedupe spans) | `seed_growth_015.py:74,100,338` | 18 / `63c1d12` | words −46.7%; 20 → 19 | Rendering → Phase 3 (recall) |
| 016 | Compact stability, 3 replicates | `seed_growth_016.py:41,93,338` | 19 / `0cfbfb7` | 60/60 vs 59/60 calls; majority 20/20 | Parity reference for 014 + 015 |

### Beliefs (stores; all P, det)

| Mechanism | Rule | Where | Evidence |
|---|---|---|---|
| Contradiction proposal | `ContradictionProposed` pins the exact belief `(id, version, seq)` plus decision plus outcomes. `authority:"proposal_only"` | `mnexa_seed.py:2844, 3075` | ledger 38; `e4c3d74`; 9 tests |
| Contestation | Quorum ≥ 2 distinct decisions agreeing on the same normalised contradiction, pinned to the head version. Creates a new version with the same text and `status:"contested"` | `mnexa_seed.py:3266` | ledger 38 |
| Recall suppression | Heads as-of N; heads whose status is `contested` or `superseded` are skipped; there is no fallback to an older version | `mnexa_seed.py:851, 942` | ledger 38 |
| Supersession | Old head must be contested. The replacement is a different proposition, promoted by its own quorum. ≥ 2 **shared** counter-decisions are required. Creates a new version with `status:"superseded"` and `superseded_by*` | `mnexa_seed.py:3602` | ledger 39; `fb1bb8f`; 8 tests |

**Bugs found by reading the code** (none of these are tested in MNEXA). Porting fixes them; each fix is listed as a
deliberate deviation in D-0017:
- (a) promotion can silently re-activate a contested or superseded belief;
- (b) a retry returns a stale v1 instead of the head;
- (c) contest does not short-circuit on `superseded`;
- (d) version pins live only in metadata.

### Episodes

| Mechanism | Where | Evidence | Port |
|---|---|---|---|
| Episode identity, anchors, membership, authority, timing | A: ADR-0009 (Q-1…Q-15); open D-25, D-26 | **none** (no code) | **Implement fresh** from ADR-0009. There is no parity target, so it is gated by invariant tests (D-0017) |

### Not in Phase 2 (for the record)

- **Recall 017–029, ContextFrame, and the transplant 030/031** → Phase 3.
- **Capabilities** (`mnexa_capability.py`, untracked; 033–035; `overall_pass=False` in 033 and 034) → with skills,
  later.

## 2. The regression suite: what gates what

Proposed; decided in D-0016.

| Level | What | Sets | Model calls |
|---|---|---|---|
| L1 grader parity | Nacre's port of `grade_text` re-grades MNEXA's **stored** decisions and must reproduce MNEXA's recorded pass counts **exactly** | 003–016 | 0 |
| L2 mechanism parity | MNEXA's **stored raw model responses** are fed into Nacre's gates. Admissions, rejections and fallbacks must equal MNEXA's recorded ones **exactly** | 007–016 (the ones with stored raw responses) | 0 |
| L3 live parity | The full Nacre pipeline with a live model, k = 3 replicates, within margins fixed in D-0016 | 014, 015, 016 (the final pipeline) | live, run by the owner |
| Belief lifecycle | MNEXA's 48 lifecycle tests, translated to the Nacre API, plus the deviation tests | ledger 34–39 | 0 |

## 3. What was frozen on 2026-10-01

Frozen **before any port code**, in `tests/regression/mnexa/`:
- **Copied byte-identical** (verified with `cmp`):
  - `tasks.json` and `tasks_003`–`016.json` with their `.sha256` sidecars. All sidecars match.
  - Both diagnostic grader files.
  - All 29 seed-run `result.json` files (001–029) and the two regrade files.
- **`MANIFEST.json`** records:
  - MNEXA HEAD and its dirty state;
  - the sha256 of every task set 003–029;
  - the sha256 of every result;
  - the sha256 of every per-run SQLite state file and of the 031 workspace, which stay in MNEXA (about 9 MB each).
- **Left in MNEXA, frozen by hash only:**
  - recall task sets 017–029 (tracked in MNEXA git);
  - results 030–035.

  The results contain provider response ids that the repo secret scan flags as high-entropy. They are false
  positives, but I did not add allowlist entries without asking.
