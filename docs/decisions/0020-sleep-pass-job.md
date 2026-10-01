# D-0020: The sleep-pass job (consolidation)

- **Status:** proposed
- **Tier:** D2 (cross-module behaviour; a new job with model calls)
- **Date:** 2026-10-01
- **Relies on assumptions:** A-0003, A-0026, A-0027, A-0029 (A-0023 superseded)

## Context
SPEC defines the sleep pass as an offline job, per scope, only where flagged events exist. It proposes lessons with
evidence-disciplined consolidation, grounds every claim in exact spans behind an authoritative-role gate with atomic
propositions, admits closed-world only, keeps fallback records, and merges, contests and supersedes.

MNEXA's proven version of this is the **final pipeline of seeds 011–016** (014 without compaction: 20/20, 0 unsafe).
It exists only in experiment scripts (inventory §0.1). Its oracle-dependent steps (006 atoms, 008 in-text roles,
009 boundaries) cannot be ported as they are.

## Options considered
1. **Port each seed as a separate stage, 005 through 016.** Maximum fidelity to history, but it would port
   superseded variants (006 and 009 need oracles; 010 was negative).
2. **Port the final pipeline (recommended).**
   - Pipeline: 005 discipline + 011 structured propositions + 012 repair + 007 exact spans + 008 role gate (with D-0018
     authority) + 014 support-first fallback + 006 closed-world admission.
   - Every stage is checked by L2 against MNEXA's stored responses.
3. **A new single-prompt consolidator.** Cheaper and simpler, but it discards the proven gains. Rejected.

## Decision (proposed)
Option 2.

### Trigger
- `run_sleep_pass(scope)` is invoked explicitly: by an operator or cron in Phase 2, and immediately after an
  `incident`-mode flag.
- It processes flags committed after the scope's last `memory_event` op `sleep_pass_completed{through_seq}`.

### Identity
- It runs as a registered system principal with a grant on the scope, through the normal door
  (`open_scoped_session`).
- Runtime writes use `actor_kind = system`. Model outputs are `result` events with `actor_kind = model` (D-0022).

### Per flagged episode (decision plus its outcomes)
1. **Evidence bundle.**
   - The decision text is labelled as history, never truth (005).
   - Outcome sections carry their role and computed authority (D-0018).
2. **Propose.** Model seat `proposer`: MNEXA's 011 instruction with 005 discipline. It returns
   `{propositions:[{source_quote, nucleus_quote, qualifiers:[{type, source_quote}]}]}`.
3. **Repair.** Model seat `repair`: MNEXA's 012 instruction, with operations `KEEP | SPLIT | REMOVE_QUALIFIER |
   REATTACH_QUALIFIER`. The final `propositions` list is re-grounded.
4. **Support-first admission** (014), deterministic:
   - stage 1: support must be an exact, unique, contiguous quote inside an **authoritative** section. Otherwise it is
     a hard reject, with MNEXA's reason codes.
   - stage 2: structure — the nucleus is inside the support, qualifiers are typed, and the proposition is atomic.
   - stage 3: valid support with a failed structure becomes a `fallback` record (`structure_status: unresolved`),
     unless the support is already covered.
5. **Write.** One `lesson_proposed` per admitted proposition or fallback, with pinned span edges. Written in one
   transaction per episode.
6. **Promote.** The quorum rule (D-0017) runs for each proposal text touched.

### Contradictions in Phase 2
- A trusted `correction` whose `correction_of` targets a belief-version event produces `contradiction_proposed`.
  Contest and supersede then run under the D-0017 rules.
- Automatic detection needs to know which belief informed a decision (ContextAssembled, Phase 3).

### Idempotency and crashes
- Proposal keys are `(flag_event_id, stage, request_sha256)`. A crashed pass re-run finds its recorded model calls
  and appends nothing twice.
- `sleep_pass_completed` is written last.

### Deliberately not in Phase 2
- a checker model (see question 1);
- duplicate merging beyond exact normalised text;
- decay, protection, habituation and pattern compression (Phase 4);
- compact rendering (015, Phase 3 recall).

## Revision after EXP-0001 (2026-10-01, proposed; owner to approve)
EXP-0001 (B = 178/180) and the shortcut audit (`docs/plans/phase-2-shortcut-audit.md`) suggest these changes.

- **R1. No injected candidates in the product pipeline.**
  - MNEXA's `ensure_fallback_challenges` appends task-author candidates (one of which contains the answer clause in
    all 60 families) to the model's proposals before admission.
  - In Nacre, admission sees **only** the model's proposals.
  - Safety challenges run through the same deterministic admission in a separate, discarded check in the evaluation
    harness.
  - Gate checks: product modules never receive task fields, and injected text never reaches memory.
- **R2. Prompt parity against recorded MNEXA prompts (new deterministic check, 0 model calls).**
  - EXP-0001 recorded MNEXA's exact extraction and repair prompts (`tests/regression/exp0001/calls.jsonl`, 360
    formation calls).
  - For each family and run, Nacre's proposer and repair prompts, built from the mapped capture events, must be
    **byte-identical** to MNEXA's. Where a difference is required (no role markers in the text, because authority
    now comes from the envelope), it is listed as a named, reviewed prompt delta, and L2 shows it does not change
    admissions.
- **R3. Recorded end-to-end formation parity.**
  - Replay EXP-0001's recorded extraction and repair responses through Nacre's pipeline (`RecordedProvider`).
  - The admitted structured propositions and fallback records must equal MNEXA's for that run. Comparison excludes
    injected candidates, and accounts for R2's named deltas.
  - This extends L2 from MNEXA's 007–016 history to the exact pipeline and model of the frozen baseline.
- **R4. Fallback is load-bearing.** In 33/180 baseline trials the answer reached memory only through
  support-first fallback (model-proposed, valid support, failed structure). Fallback is mandatory in the port, not
  optional.
- **R5. Measured cost, replacing the estimate.**
  - 2 model calls per flagged episode, about 1,055 input and 228 output tokens per call (about 2,110 / 456 per
    episode), median latency 3.0 s per call.
  - Measured on `gpt-4o-mini-2024-07-18` in EXP-0001.
- **R6. The transfer step is evaluation only in Phase 2.** The reasoning seat (`make_fidelity_reasoner`) belongs to
  the harness and is identical for B, C and N. Product recall and reasoning are Phase 3.

## Why this one
- It is the only option that ports what MNEXA measured.
- Each stage has a deterministic zero-margin check (L2) against MNEXA's own stored model outputs.
- The oracle problem is solved by D-0018's envelope authority rather than by a model.

## Consequences
- **To build:** `sleep/` has five functionality files plus the job. Seat prompts are ported verbatim from MNEXA and
  frozen by hash.
- **Cost:** 2 model calls per flagged episode, recorded with token counts.

## How we'd know it was wrong
- L2 passes but L3 fails, which means drift outside the gates (prompt assembly, evidence bundling).
- Fallback volume dominates in real use.

## Owner answers (2026-10-01)
- **Checker model: deferred.** Later it is its own experiment with its own gate, and it ships only if it improves
  results.
- **Repair** stays part of the final pipeline (014–016 scope, D-0016 amendment 2).

## Original questions
1. SPEC says a different model checks the work. MNEXA never had a checker; its closed-world span admission is a
   deterministic checker. Should the model checker be deferred to a later, separately measured ADR, so parity is
   measured on the MNEXA pipeline?
2. Seed 012 on its own lowered transfer (12 → 10) and admitted one invented nucleus. The final pipeline (014–016)
   includes repair and scored 20/20 with 0 unsafe admissions. Port repair as part of the final pipeline?
