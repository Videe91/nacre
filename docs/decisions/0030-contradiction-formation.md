# D-0030: Contradiction formation — linking a trusted correction to the belief it corrects

- **Status:** proposed (2026-10-02). Not built. Owner review requested before any code.
- **Tier:** D2 (cross-module behaviour: sleep pass, stores; a new model seat). It also touches **what counts as
  independent support** (D-0017), which the owner may treat as **D3**. Flagged in question 2.
- **Date:** 2026-10-02
- **Relies on assumptions:** A-0027 (quorum rules); new A-0047, A-0048 (below)
- **Amends:** D-0017 (support and contest grouping), D-0020 (§ "Contradictions in Phase 2").

## Context
- **Owner (2026-10-02, EXP-0004 E1):** "a trusted correction of a belief becomes a contradiction proposal, per
  D-0020". The τ dev run waits for this, because a contested belief can never make coverage strong (D-0025 §6).
- **What exists:**
  - `propose_contradiction` (pinned to the head version);
  - `contest_belief`: quorum ≥ 2 distinct decisions with the **same normalised contradiction text**;
  - `supersede_belief`: the old head is contested, the replacement is active, and ≥ 2 decisions both contradict
    the old head and support the replacement;
  - all are tested.
- **Nothing calls them in production.**
  - D-0020 names one trigger, "a trusted `correction` whose `correction_of` targets a belief-version event". It is
    not built.
  - D-0020 defers automatic detection until "which belief informed a decision" is known (ContextAssembled, Phase 3).
- **What changing facts look like** (the EXP-0004 dev split, checked 2026-10-02; the same shape in every T3
  family):
  - **v1:** an episode whose outcome holds an authoritative correction section, e.g. "… need a 1750 ms
    lock_timeout …".
  - **v2:** two or more LATER, independent episodes (different days and authors). Their outcomes hold authoritative
    corrections that state a different value, each worded differently:
    - "… migrations now set 100 ms; the previous value stalled writes …";
    - "… use a 100 ms lock_timeout from now on …".
  - **No reference to v1:** a v2 correction does not reference v1 or any belief. Agents and reviewers do not know
    belief ids, and these histories carry no ContextAssembled traces.
  - **What v2 shares with v1:** the identity address (`code:…/parser.py`, D-0018 amendment 2) and the decision
    text.
- **Two gaps follow:**
  1. **Trigger:** nothing links a v2 correction to the v1 belief.
  2. **Grouping:** each v2 correction is authoritative, so each becomes its own single-source belief (D-0017
     amendment 1) with its own text. D-0017 groups support and contradictions by **identical normalised text**, so
     the two paraphrased v2 episodes never reach the contest quorum, and "≥ 2 shared decisions" for supersession can
     never hold. The set was built with two v2 episodes "so the normal contest and supersede path can apply", but
     that path cannot see paraphrases.

## Options considered
1. **A — A grounded relation judge in the sleep pass, with grouping by belief (recommended).**
   - **Candidates (deterministic):** after an episode's lessons are admitted, find every active or contested belief
     in the same stream that shares at least one identity address of type `code`, `file` or `entity` with the
     episode's events. There is no candidate without a shared address, and at most K = 5, nearest-ranked first.
   - **Judgement (one model call per episode that has candidates):** for each (admitted lesson, candidate belief)
     pair, the seat answers one of:
     - `same_claim`: they state the same fact;
     - `contradicts`: they state incompatible values for the same thing;
     - `unrelated`.

     Each `same_claim` or `contradicts` answer must quote the exact deciding span from the episode's
     **authoritative** correction section.
   - **Grounding (deterministic; any failure is a hard reject, recorded):**
     - the span is exact, unique and contiguous inside an authoritative section of this episode (the
       support-first rule, D-0020 §4);
     - the belief is a current head in this stream that shares an address;
     - the episode's decision has an observed outcome.
   - **Effects (existing stores, with two D-0017 amendments):**
     - `same_claim`: the lesson is recorded as support **for that belief** (the proposal carries the belief's
       object id, with an edge `same_claim_as`). It counts toward that belief's quorum. Its own text stays the
       grounded span.
     - `contradicts`: `propose_contradiction` against the belief's head, **grouped by the lesson's resulting belief
       (the replacement)** instead of by identical text. Contest then needs ≥ 2 distinct decisions whose
       contradictions point to the same replacement. Supersession is unchanged: the old head is contested, the
       replacement is active, and ≥ 2 shared decisions.
   - **Outcome on T3:**
     - v2a contradicts v1 and creates belief v2;
     - v2b is `same_claim` as v2 (support) and contradicts v1;
     - v1 is contested (2 decisions), then superseded by v2 (2 shared decisions), so recall shows v2 only.
   - **Pros:** it keeps quorum 2 for contest and supersession; one model judgement never flips a belief alone; it
     handles paraphrase.
   - **Cons:** a model judgement now feeds support and contest grouping (bounded by grounding and quorum); about
     one more sleep call per episode with candidates.
2. **B — Authoritative single-source supersession.**
   - **How:** one grounded `contradicts` judgement from a later authoritative correction both contests and
     supersedes, mirroring single-source promotion (D-0017 amendment 1).
   - **Pros:** simpler; no change to support grouping.
   - **Cons:** one wrong model judgement silently replaces a correct belief, and the set's two counter-episodes go
     unused. Lost on safety.
3. **C — Deterministic address-and-value conflict, no model.**
   - **How:** two lessons sharing an address and differing in a number or identifier are treated as contradicting.
   - **Cons:** it cannot tell a conflicting value from a different fact about the same file (a lock timeout vs a
     retry count), so it would contest correct beliefs. Lost.
4. **D — Recency at recall** (the newest authoritative lesson wins, with no contest). A silent override of D-0017
   with no evidence trail, and stale facts are hidden rather than recorded as contested. Lost.
5. **E — Only D-0020's explicit trigger** (`correction` events whose `correction_of` names a belief version).
   - It is still built, as a deterministic path (no model).
   - It never fires on EXP-0004 or on agents that do not know belief ids, so on its own it leaves the stale-fact
     ceiling to chance.

## Decision (proposed)
**Option A, plus option E's explicit trigger.**
- **Explicit (deterministic):** a trusted `correction` event whose `correction_of` targets a belief-version event
  produces a `contradiction_proposed` against that belief's head, grouped by the correction's own admitted lesson
  when there is one.
- **Implicit (option A):**
  - a new sleep step `sleep/judge_relations.py`, a model seat on the sleep model with a hash-pinned prompt;
  - it runs inside the episode's transaction after admission and before promotion;
  - every call is recorded through `call_model` (D-0021, D-0022).
- **D-0017 amendments:**
  - (i) support may be attributed to an existing belief by a grounded `same_claim` judgement (edge
    `same_claim_as`), counted as one decision toward that belief's quorum;
  - (ii) contradiction proposals carry `replacement_object_id`, and contest groups by it (or by normalised text when
    there is no replacement, the explicit path's old rule);
  - quorum values are unchanged (2), and supersession is unchanged.
- **Authority:**
  - only authoritative sections (D-0018 `section_authority`) can ground `same_claim` or `contradicts`;
  - tool output and other untrusted sections never can, so an injected "ignore the reviewer" cannot contest
    anything.
- **D-0023:** contradiction and support events are keyed by their sources (the decision, outcome and action, plus
  the belief head), as `propose_contradiction` already does.
- **Also fixed:** `propose_contradiction` resolves outcomes recorded against an action (D-0020 amendment 1). Today it
  only follows a direct `outcome_for`.

## Measurement before EXP-0004 (dev split = working data)
These numbers are reported with the τ dev run, which needs live sleep calls and so is the owner's run.
- **(a)** T3 resolution: the share of dev T3 families whose v1 ends superseded by v2 after the history.
- **(b)** False contests: beliefs contested or superseded that the set does not mark as v1 (the distractors and
  every other fact).
- **(c)** Judge cost per episode.

**Thresholds, fixed before the dev run** (owner to confirm or change, question 3):
- (a) ≥ 0.90;
- (b) = 0;
- otherwise the test run waits and the gap is reported (no patching against dev without saying so).

## Why this one
- It answers the owner's framing (a trusted correction of a belief becomes a contradiction proposal) with the
  trigger the data actually has: a shared identity address plus a grounded judgement.
- It keeps every MNEXA safety rule (quorum 2 to contest, shared-decision supersession, no re-activation) and
  extends only the grouping, so paraphrases count.
- One model mistake cannot change a belief's status; two independent authoritative episodes are still needed.

## Consequences
- **New:**
  - `sleep/judge_relations.py` (seat, prompt pin, grounding);
  - changes in `sleep/run_sleep_pass.py`, `stores/propose_lesson.py` (`same_claim_as`),
    `stores/promote_if_supported.py`, `stores/propose_contradiction.py` (`replacement_object_id`; action
    resolution), `stores/contest_belief.py` (grouping) and `stores/supersede_belief.py` (calls in the episode
    flow);
  - tests per rule, plus an end-to-end T3 test on a synthetic family;
  - the measurement above.
- **Cost:** about one extra sleep call per episode that has address-sharing beliefs.
- **Recall:** contested items render as "CONTESTED, not established" until supersession (D-0025 amendment 1), and
  superseded heads are excluded.
- **Not in scope:** contradictions from recall traces (which belief informed a decision); a later ADR can add that
  as a second trigger.

## How we'd know it was wrong
- False contests on dev or in production: the judge links unrelated lessons that share a file. Response: tighten the
  candidate rule or the grounding; never lower the quorum.
- T3 families stay unresolved because the judge misses paraphrases (A-0048).
- Address gating misses real conflicts stated without a shared address (A-0047).

## New assumptions
- **A-0047:** a correction that changes a fact shares at least one `code`/`file`/`entity` identity address with the
  belief it corrects. **Test:** the dev split's T3 families (all share one), then production sampling.
- **A-0048:** the relation judge, grounded and quorum-gated, yields no false contests and resolves ≥ 90% of changing
  facts. **Test:** the measurement above.

## Questions for the owner
1. Approve option A plus the explicit trigger (E), or choose another option.
2. **(possibly D3)** Approve that a grounded `same_claim` judgement may attribute support to an existing belief
   (paraphrase support), and that contest groups by the replacement belief. Quorum values are unchanged.
3. Confirm the dev-run thresholds: T3 resolution ≥ 0.90 and zero false contests. Also confirm that failing them
   blocks the test run.
4. K = 5 candidate beliefs per episode, and the address types `code`/`file`/`entity`. Confirm, or set other values.
