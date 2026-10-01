# D-0018: Capture event payloads (decision, prediction, action, outcome, correction)

- **Status:** accepted (owner, 2026-10-01). **D3 part APPROVED by the owner 2026-10-01: outcome authority by source and trust.**
- **Tier:** D2 (persistence format, public interface). The authority rule for outcome sections is **D3**
  (injection boundary).
- **Date:** 2026-10-01
- **Relies on assumptions:** A-0012, A-0026

## Context
- **SPEC:** "Each cycle records decision, prediction, action and outcome as separate evidence (ADR-0016). Absence of
  an outcome is absence of evidence, never failure."
- **Envelope:** the Phase 1 envelope already has these event types, plus `cycle_id`, `caused_by`, `actor_model*`,
  `trust` and `mode`. What is missing is the **body**: the content fields, and the typed references between events.
- **What MNEXA implemented:** only `DecisionMade` and `OutcomeObserved`, with `outcome_for` and `decided_from`.
- **What MNEXA ADRs define but never built:**
  - prediction and action (ADR-0016);
  - the structural reference vocabulary (ADR-0008): `outcome_for, execution_of, response_to, correction_of,
    evaluates_prediction, continuation_of`. Causal names are forbidden.
- **Why authority matters:** MNEXA's strongest consolidation result (the 008 role gate: 9 → 18) depended on roles
  **pre-marked inside the text**. Real outcomes carry no such markers. Nacre needs a way to know which part of an
  outcome is authoritative.

## Options considered
1. **Free text bodies**, with roles inferred by a model.
   - Pros: simple capture.
   - Cons: this reintroduces 010's negative result, where autonomous boundaries fell 18 → 14. A model deciding
     authority is also an injection hole.
2. **In-text role markers**, as in MNEXA 008.
   - Pros: exact MNEXA parity.
   - Cons: any writer can type a marker, so an untrusted tool output could claim to be an authoritative correction.
3. **Structured bodies with typed sections; authority from the envelope (recommended).** Outcome bodies carry
   explicit sections. Whether a section *counts* as authoritative is decided by the runtime from `event_type`,
   `source`, `trust` and `actor_kind`. It is never decided by the text or a model.

## Decision (proposed)
Option 3.
- **Format:** every body is `payload_type = structured` (deterministic CBOR, D-0008).
- **Refs:** typed references are a list `refs: [{rel, event_id}]` using the MNEXA ADR-0008 vocabulary.
  - Backward-only, same stream, no self-reference.
  - Validated at append against committed events.
- **Envelope `caused_by`** keeps its D-0002 meaning, and is set to the primary ref.

| event_type | Body fields | Required refs |
|---|---|---|
| `decision` | `decision_text`, `decision_kind`, `decided_from` (ContextAssembled id; **optional in Phase 2**, required once recall exists), `context_evidence_sha256?`, `reasoning_owner` = `external` | — |
| `prediction` | `expected_outcome`, `predictor` = `agent` \| `predictor`, `confidence?` | `evaluates_prediction` is on the *outcome*; a prediction refs `response_to` its decision |
| `action` | `action_kind`, `description`, `dispatched` = true (ADR-0016: the action boundary is dispatch) | `execution_of` → decision |
| `outcome` | `success` = true \| false \| null, and `sections: [{role, text}]` with role ∈ `status`, `evaluation`, `correction`, `diagnostic`, `operator_note` | `outcome_for` → decision or action; optional `evaluates_prediction` |
| `correction` | `text`, `scope_of_correction` | `correction_of` → any earlier event |

**Authority rule (D3).** A section is **authoritative** only when all of these hold:
- its role is `correction`, or `evaluation` with `success` false;
- the event is `trust = trusted`;
- the event's `source` is `ci`, `review` or `git`, or its `actor_kind` is `person`.

Everything else is non-authoritative evidence:
- `status`, `diagnostic` and `operator_note` sections;
- any section from `web` or `tool` sources, or from untrusted events.

This replaces MNEXA 008's in-text markers. Only authoritative spans can ground a lesson (D-0020).

**Other rules:**
- A missing outcome is recorded as nothing. No outcome row, no `success=false`.
- A person's `correction` event whose correction text is a direct statement is also a write-gate signal (D-0019).
- MNEXA ADR-0016 rule 2a: when the committed decision differs from the raw model output, the raw output is kept as a
  `result` model-call event (D-0022), and the decision refs it with `response_to`.

## Why this one
- Authority comes from facts the runtime controls (source and trust are set at intake, D-0012), so text cannot
  forge it.
- It is the only option that keeps MNEXA 008's gain without its oracle.

## Consequences
- **To build:**
  - `capture/` functionalities, one per event type;
  - reference validation at append (backward, same stream, known rel);
  - an L2 mapping from MNEXA 008–016's role-marked evidence to sections, used only in the evaluation harness.
- **Behaviour change:** untrusted tool output can never produce a lesson directly. A trusted CI failure or reviewer
  correction can.

## How we'd know it was wrong
- L3 live parity fails with the envelope-derived roles while L2 passes. Real outcomes would then not map onto
  sections cleanly (A-0026).

## Questions for the owner
1. **(D3)** Approve the authority rule (trusted plus ci / review / git / person)?
2. Should `decided_from` stay optional until Phase 3 recall exists?
