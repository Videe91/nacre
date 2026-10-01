# Phase 2: test-only shortcut audit of the pipeline being ported (MNEXA 014–016)

- **Date:** 2026-10-01.
- **Method:** an AST scan of every `family[...]` / `family.get(...)` read in MNEXA `experiments/seed_growth_008`
  and `011`–`016` (the modules the 014–016 pipeline imports), per function. Each read was traced to whether it feeds
  **memory formation** or only **measurement**.
- **Scope:** the code that ran as arm B in EXP-0001.
- **Rule for the port (owner):** product code under `src/nacre/` never reads task-author data and never receives
  anything a real deployment would not have. Task data may appear only in the evaluation harness (`src/nacre/eval/`),
  and only on the measurement side or as scenario input that both arms receive identically.

## Every task-author field the 014–016 path reads

| Field / mechanism | Where | Feeds memory? | What it is | In the port |
|---|---|---|---|---|
| `raw_source` | `013._build_repaired_structure`, `run_family_014/015/016` | yes | The outcome evidence text (the "EVALUATION RECORD") | Scenario input. The harness turns it into a real `outcome` event (D-0018). Legitimate: real outcomes have text |
| In-text role markers `<<<ROLE:…>>>` inside `raw_source` (`008.parse_role_regions`, `role_for_span`, `014.validate_authoritative_support`) | admission | **yes (authority)** | Task-author labels saying which span is authoritative | **Excluded from product.** Authority comes from the envelope (D-0018: trusted plus ci / review / git / person, plus the `correction` / `evaluation` role). The harness maps each marked region to an outcome **section** with role and source. Product code never parses markers. L2 checks the mapping reproduces MNEXA's admissions exactly |
| `ensure_fallback_challenges` (`family["fallback_challenges"]`: 1 recoverable plus 2 unsafe candidates appended to the model's proposals) | `014:986`, `015:577`, `016:540` | **yes (B's memory could include the injected recoverable candidate)** | Adversarial safety instrument written by the task author. **The recoverable one contains the answer clause in all 60 families** | **Excluded from product memory.** In Nacre's harness the challenges go through the **same deterministic admission gate in a separate, discarded check**, and the counts are reported as safety metrics. Injected candidates never reach memory used for transfer |
| `candidate_decision` with `_controlled_candidate_reasoner` | `_run_memory_condition` | indirectly | A forced first (failed) decision, the scenario setup | Scenario input recorded as a real `decision` event; the same for every arm. Not a product path |
| `success=False` on the outcome | `_run_memory_condition` | indirectly | Scenario setup | Recorded as the outcome's `success`; the same for every arm |
| `entities` | `decide(..., entities, ...)` | recall addressing | Task identity tags | Recall is Phase 3. In Phase 2 the harness supplies them to the MNEXA-style transfer step for every arm |
| `transfer.prompt` with `make_fidelity_reasoner` (`FIDELITY_INSTRUCTION`) | transfer | no (it is the test) | The evaluation task and the reasoning-seat prompt | Harness only; identical for B, C and N |
| `semantic_clause` | `013._build_repaired_structure` (metric key only), `014:1058,1146` (`*_clause_survives`) | **no** (checked: only written into reported metrics) | The grader's target clause | Harness metrics only. A test asserts that no product module receives it |
| `semantic_grader`, `knowledge_grader`, `semantic_dimension`, `semantic_operator` | grading and reporting | no | Graders and labels | Harness only (L1 port of `grade_text` / `semantic_grade`) |
| `atomic_units`, `expected_qualifiers`, `structured_compound_challenges` | 011/012 metrics only (`_score_structure`, `count_compound_*`); not read on the 014–016 path | no | Oracle structure for scoring | Harness only (L2 metrics) |
| `authoritative_atoms` (008), oracle atoms (006), canonical boundaries (009) | 006/008/009 conditions only | yes, in those seeds | Oracles | **Not ported.** These seeds are covered by L1/L2 on MNEXA's stored outputs only |

**Not test-only (MNEXA's real behaviour, ported as is):**
- the lesson text passed to `consolidate()`, which is the pipeline's own rendered admitted memory;
- the closed-world admission rules;
- the support-first fallback.

## Did B depend on the injected answer?

From EXP-0001's frozen record:
- In **147/180** trials the answer clause reached memory through **model-proposed structured** propositions.
  Injected recoverable candidates cannot be admitted as structured: their structure is invalid by design.
- In the remaining **33**, it reached memory through fallback records. In **all 33**, the model's own extraction or
  repair output **in the same run** contained the clause (matched by call time window and source text).

**Reading:** strong evidence that B did not get knowledge from the injection. It is not proof: both copies are the
same source span, and MNEXA's record does not say which copy was admitted.

**Options for the owner:**
- **(a)** Accept B = 178/180 as frozen. Nacre's arm runs without injection into memory; the asymmetry can only work
  **against** Nacre.
- **(b)** Run EXP-0002 first: MNEXA's harness on derived task copies with `fallback_challenges` emptied (data only,
  harness unmodified), with safety measured by the deterministic gate.
  - Cost: about $1.
  - It would replace B only if the owner decides it should.
  - It would need its own pre-registration.

**Recommendation:** (a). The bias runs against the system under test, which is the safe direction for a
non-inferiority claim. The attribution evidence says the effect is small.

## Port confirmation

These are enforced as Phase 2 gate checks, not intentions:
1. **No product module parses role markers, reads task files, or receives task-author fields.** Check: a test imports
   every `src/nacre/` module except `src/nacre/eval/`, and asserts none references `raw_source`, `semantic_clause`,
   `fallback_challenges`, `<<<ROLE`, `candidate_decision`, `grader` or `tasks_0`.
2. **Injected safety challenges never reach memory.** Check: after a harness run, no `memory_event` body contains a
   challenge candidate's text unless the model proposed it in that run (from recorded model calls).
3. **Authority comes only from the envelope.** Check: L2 with the marker-to-section mapping reproduces MNEXA's
   admissions exactly. A canary maps every section as untrusted, and then nothing may be admitted.
