# D-0017: Interpretation-plane schema (proposals, beliefs, statuses, ancestry, episodes)

- **Status:** accepted (owner, 2026-10-01), with amendment 1 (single-source promotion). **D3 part APPROVED by the owner 2026-10-01: no content plaintext in the projection (keyed content MAC).**
- **Tier:** D2 (persistence format, invariants). The privacy part (what the projection may hold in plaintext) is
  **D3**.
- **Date:** 2026-10-01
- **Relies on assumptions:** A-0003, A-0014, A-0027

## Amendment 1 (owner, 2026-10-01): single-source promotion, with safeguards
**Why:** every frozen task holds one failed episode, so a quorum of two never forms and nothing would be learned.

**Rule:** a lesson may promote on **one** episode only when **all** of these hold:
1. Its support comes from a **trusted correction**: an outcome `correction` section that is authoritative under
   D-0018. The event is `trust = trusted` by source **and** authorship (D-0012), and its source is ci / review / git,
   or its actor is a person.
2. It is **grounded in the correction's exact span**: the proposal's support is an exact, unique span inside that
   section.

**How such a belief is marked:**
- Origin `stated` and `support = single_source`, with **lower starting values** than a quorum belief:
  - confidence 0.5 (quorum: 0.8);
  - strength 0.5 (quorum: 1.0).

  These are placeholders, tuned in Phase 4. Recorded on the version.
- **Upgrade:** a second agreeing episode (another decision whose trusted correction or outcome supports the same
  normalised text) creates a new version with `support = quorum` and the quorum values.
- It is **contestable exactly like any belief**: contradicting outcomes contest it by the normal quorum (D-0017
  "Contest") and can supersede it. It gets no protection.

**Unchanged:** lessons inferred from outcomes (anything not grounded in a trusted correction span) still need **two
agreeing decisions**.

**Tests:** one per condition:
- untrusted correction: no promotion;
- trusted but ungrounded: no promotion;
- trusted and grounded: single-source values;
- second episode: upgrade;
- contestable and demoted;
- an outcome-inferred lesson stays at quorum 2.

## Context
- **SPEC:** "Two planes (MNEXA ADR-0002)". Interpretations (beliefs, episodes, …) are versioned and always linked
  back to experience. Memory records have status `proposed / active / contested / superseded / fallback`.
  Consolidation errors are mitigated by "full rebuild from the ledger".
- **What MNEXA did:** one SQLite `commits` table for both planes. Interpretive versions are rows `(object_id,
  version)`. The status sits in `metadata` and is **absent** when active. Refs are bare ids, with version pins only
  in metadata.
- **What MNEXA ADRs require but never implemented:**
  - pinned derivation edges inside the version's content hash (ADR-0005);
  - staleness computed, never stored (ADR-0006);
  - three canonical objects (ADR-0007);
  - episodes (ADR-0009).
- **Nacre constraints:**
  - Everything is scoped (RLS), encrypted per data key, and shreddable (D-0004, D-0005).
  - The ledger is the only immutable truth (D-0002, D-0003).

## Options considered
1. **Ledger only.** Every interpretation version is a ledger event; heads are computed by scanning.
   - Pros: one truth; shredding and scoping for free.
   - Cons: head and status queries scan and decrypt events. Too slow for recall in Phase 3.
2. **Separate interpretation tables are the truth.** Append-only version rows, written in the same transaction as a
   ledger audit event.
   - Pros: fast.
   - Cons: two truths. Plaintext outside the ledger breaks crypto-shredding unless it is separately encrypted.
     Rebuild is impossible.
3. **Ledger is truth; tables are a rebuildable projection (recommended).**
   - Every interpretation version is a `memory_event` (op `version`): encrypted, sealed, scoped.
   - A projection schema `interp` holds only structural fields in plaintext: ids, versions, status, pinned edges,
     hashes and commit_seq.
   - The projection is written in the **same transaction** as the event.
   - Content is read by decrypting the event, so shredding the key removes the content everywhere.

## Decision (proposed)
Option 3.

### Kinds (Phase 2)
All three canonical objects follow MNEXA ADR-0007.

**No new event type is needed.** SPEC already defines `memory_event` as "proposal, promotion, contestation,
supersession". Every interpretation event is `event_type = memory_event`, `payload_type = structured`, with a body
`op`:

| `op` | Plane | Meaning |
|---|---|---|
| `lesson_proposed` | historical | a proposal; never recalled |
| `contradiction_proposed` | historical | pins one belief version |
| `episode_proposed` | historical | a boundary proposal |
| `flag` | historical | a write-gate flag (D-0019) |
| `version` | interpretive | one new version of a `belief`, `fallback` or `episode` object |

Capture events use the existing types (D-0018). Model calls use `result` events (D-0022).

### Version record
Carried in the event body, with the structural subset in the projection:
- **Identity:** `object_id` (UUIDv7, opaque; for beliefs, an identity derived as in MNEXA from the normalised text
  within the scope), `version`, `kind`.
- **`status`:** `active | contested | superseded | fallback`. Always explicit, never absent.
  - "Proposed" is a separate historical event, not a status. This follows MNEXA ADR-0002. SPEC's
    `proposed` status maps to it; SPEC note.
- **`edges`:** a pinned, ordered list of `{role, target_event_id | (target_object_id, target_version),
  span?: {start, end, sha256}}`.
  - Roles: `support`, `contradiction`, `superseded_by`, `derived_from`, `member` (episode).
  - Edges point only to targets that already exist (commit order, MNEXA ADR-0005 L-7).
  - **The edge set is part of `content_sha256`** (L-9).
- **Content:** `nucleus`, `support_text`, `qualifiers[]`, `origin` (`stated | observed | inferred`), `confidence`.
  These are encrypted, in the event body only.
- **Staleness:** never stored. It is computed as MNEXA ADR-0006's four states when read (Phase 3 recall carries it).

### Transitions
All are MNEXA rules unless marked. Each is one new version appended atomically with its event:
- **Promote:** quorum ≥ 2 distinct decisions with the same normalised text and valid decision → outcome ancestry.
  - A new belief is created at v1.
  - New independent support gives v(n+1) with the text unchanged.
- **Contest:** quorum ≥ 2 distinct decisions with the same normalised contradiction, pinned to the head version.
- **Supersede:** the old head is contested, the replacement is an active and different proposition, and there are
  ≥ 2 shared counter-decisions.
- **Recall read (Phase 3):** heads as-of N, excluding `contested` and `superseded`, with no fallback to older versions.

### Deliberate deviations from MNEXA
These are bugs in MNEXA; each gets a test:
1. **Promotion never re-activates a contested or superseded belief.** Support after contestation becomes a new
   support edge on the contested version, still contested. Only supersession changes the status.
2. **A retry returns the current head**, never a stale version.
3. **Contest is a no-op on `superseded` as well as `contested`.**
4. **Version pins are real edges, not metadata.**

### Episodes (fresh implementation of MNEXA ADR-0009)
- Opaque id, versioned.
- Membership is an ordered set of event ids; that set is the version's edge set.
- Anchors (task_id, cycle_id, session) are used for candidate generation only.
- Episodes are formed **only** in the sleep pass. A model may propose boundaries; the runtime validates
  admissibility and commits.
- D-25 (split and merge) and D-26 (revise vs new lineage) stay deferred. Phase 2 always creates a new lineage unless
  a proposal names an existing one explicitly.

### Tables (`interp` schema, migration 0009)
- **`interp.versions`:** `object_id, version, kind, status, stream_id, event_id, commit_seq, content_sha256`.
- **`interp.edges`:** `object_id, version, ordinal, role, target_event_id, target_object_id, target_version,
  span_start, span_end, span_sha256`.
- **`interp.heads`:** a view.
- **Protection and access:**
  - RLS by `stream_id`, exactly like the ledger.
  - Append-only triggers.
  - Written by `nacre_app` inside the same append transaction.

### Rebuild
`rebuild_projection` drops the scope's projection rows and replays its `memory_event` events. The result must be
identical (gate item).

## Why this one
- It keeps one truth (the ledger), so shredding and scoping stay correct with no extra machinery.
- It still gives indexed head and status queries.
- It implements the MNEXA ADRs that MNEXA itself never built.

## Consequences
- **Needs:**
  - migration 0009 (no envelope change);
  - one functionality per transition in `stores/`;
  - the translated MNEXA lifecycle tests plus deviation tests;
  - a rebuild test;
  - episode invariant tests (Q-1…Q-15).
- **(D3) The projection holds no content plaintext.** A shredded belief keeps its structural row: ids, status and
  hashes. SHA-256 of short text could be guessed, so `content_sha256` is a keyed MAC under the data key, like
  `attachment_ref`. It is not a plain hash.

## Open question for Phase 3 (owner, 2026-10-01)
**Embeddings are content-derived and partly invertible.** They must be scoped and shreddable, never plaintext in a
shared index. This must be decided by ADR **before recall is built**.

## How we'd know it was wrong
- Phase 3 recall cannot meet latency without plaintext in the projection. That would need a new D3 decision on
  encrypted indexes.
- Rebuild is not identical.

## Questions for the owner
1. **(D3)** Approve "no content plaintext in the projection; keyed content MAC"?
2. Approve deviations 1–4?
3. Approve SPEC's `proposed` status mapping to a historical proposal event instead of a status?
