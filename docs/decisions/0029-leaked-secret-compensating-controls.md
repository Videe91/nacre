# D-0029: Compensating controls for secrets the detector misses

- **Status:** accepted in principle (owner directive, 2026-10-02). The design details below are PROPOSED: the owner
  reviews them before any code.
- **Tier:** D3 (what may be recalled or exported; the privacy boundary).
- **Date:** 2026-10-02
- **Relies on assumptions:** A-0010, A-0044, A-0045 (new)

## Context
- **Gate item 12 is NOT MET, with the risk accepted by the owner on 2026-10-02** (holdout log).
- **Final numbers:** H6 common contexts 83.8%, long-tail 46.7%, false positives 1.0%; earlier H4 88.8% and H5
  74.0%.
- **Consequence:** some secrets in credential slots will reach the ledger unredacted.
- **The owner's compensating controls:**
  1. a "report leaked secret" path: a recorded suppression event hides the event's content from all recall and
     exports immediately, with full erasure via the existing person or period shredding (D-0014, D-0023);
  2. every detected secret returns a "rotate this credential" notice to the caller.
- **Future (Phase 4/5):** evaluate a second-opinion secret classifier (a local model) on a fresh holdout.

## Decision

### 1. Suppression (proposed details)
- **The report:** `report_leaked_secret(stream, event_id, reason)` appends a `config_event`, op
  `suppress_content`, in the stream, written by the reporting principal.
  - Who may report: any principal with READ on the stream (owner question 1).
- **What the event records (no secret value, ever):**
  - `target_event_id`;
  - the reporter;
  - `reason` (`leaked_secret`);
  - an optional location hint (field and offset; never the value).
- **The projection:** `ledger.suppressions` (target_event_id, stream_id, suppression_event_id, at), insert-only,
  stream RLS. It is written in the SAME transaction as the suppression event.
- **What is hidden, from the next read on:**
  - **the target event's content:** `read_stream` and every export return a `Suppressed(event_id)` marker instead
    of the body, like `Shredded` (D-0004). The envelope and the chain stay, so verification is unaffected;
  - **every derived record whose content comes from it:** versions, proposals, model-call records and traces
    whose sources (D-0023 contributors, or edge targets transitively) include the target.
    - They are excluded from recall (the R13 merge consults suppressions inside the snapshot), and their content
      is withheld from exports.
    - Their index entries stay as ciphertext; the cache never serves a suppressed version.
- **Timing:** "immediately" means the next recall or read whose snapshot begins after the suppression commits.
  That is the same guarantee as erasure (D-0024 clarification).
- **Full erasure:** suppression does NOT destroy keys. The reporter, or an org admin, files the existing
  erase_person or forget_period request (D-0014; grace, legal holds and execution unchanged). The suppression
  event links the request id once filed.
- **Irreversible by default:** lifting a suppression needs an org-admin `config_event` op `unsuppress`, with a
  reason (owner question 2).

### 2. Rotate-this-credential notice (proposed details)
- **The notice:** every write that the detector redacted returns `notices: [{kind: "rotate_credential", rule_id,
  field, location}]` to the caller:
  - from `append_event`, the capture entries, and the MCP/SDK responses (D-0026);
  - one per finding;
  - it never contains the value.
- **Wording:** "A credential was detected and removed before storage. Treat it as exposed: rotate it now."
- **Also returned for an attachment rejected for a secret** (D-0027), which already errors.
- **Recorded:** the notice is recorded in the event's body `redactions` (already there, D-0008), so the ledger shows
  that a notice was due.

### 3. Future item (not Phase 3)
- **Phase 4/5:** evaluate a second-opinion secret classifier (a local model) on a fresh holdout, pre-registered
  before it is built. It would run only on the text the rules did not redact, and is measured for catch and FP like
  A-0010.

## Options considered
1. **Suppress by redacting in place:** rejected. The ledger is append-only and sealed (D-0003); editing breaks the
   chain.
2. **Shred immediately on report:** rejected as the default. It skips D-0014's grace and legal-hold safeguards, and
   one person's key covers more than the leaked event.
3. **Suppression as a read-time filter, then the existing shredding (chosen):** immediate effect, nothing edited,
   and erasure through the reviewed path.

## Tests (to write with the code)
1. **Suppression:**
   - after it commits, the next `read_stream`, recall and export of the target return `Suppressed`;
   - derived versions built from it are excluded from recall;
   - other events are unaffected (positive control);
   - a recall whose snapshot predates the suppression may still see it;
   - the chain still verifies.
2. **No value anywhere:** a suppression or notice event never contains the secret's value (the report's text is
   scanned).
3. **Notices:** a write with N detected secrets returns N rotate notices with rule ids and locations, never the
   values; a clean write returns none.
4. **Erasure:** a filed erase_person or forget_period after a suppression destroys the keys as today.

## How we'd know it was wrong
- Reports arrive too late to matter, so content is recalled before anyone notices: measure the time from write to
  report in Phase 5.
- Callers ignore rotate notices: measure rotation follow-through where the caller is ours.

## Questions for the owner (D3)
1. Who may report a leaked secret: any reader of the stream (proposed), or only writers and admins?
2. Is lifting a suppression allowed at all (proposed: an org admin, with a reason), or is it permanent?
3. Should suppression reach derived records transitively (proposed: yes, via D-0023 contributors and edge
   ancestry)?
