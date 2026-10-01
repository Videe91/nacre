# D-0023: Contributor-set keys, so erasure reaches every derived copy

- **Status:** proposed (owner approved option (a) in principle, 2026-10-01; this ADR needs explicit approval before
  any build)
- **Tier:** D3. It changes the privacy boundary and erasure semantics, and supersedes part of D-0004's intake rule.
- **Date:** 2026-10-01
- **Relies on assumptions:** A-0030, A-0031 (new), A-0008

## Context
Phase 2 gate item 9 is open.
- **The gap, proved by a test:** a person's data key was destroyed. Their statement became unreadable, but a
  model-call record of a prompt built from it still held their words, readable.
- **Why:** every derived record (model-call records, lesson and contradiction proposals, belief, fallback and
  episode versions, flags and run markers, and the projection's keyed MACs) is encrypted under the stream's
  system-subject key. D-0004 only reaches content stored under the person's own key.
- **Second, related gap:**
  - D-0004 says a person's key covers events "by or about that person".
  - But its intake rule gives the person subject only to `statement` and `message` events. A person's `correction`,
    `decision`, `prediction` or `outcome` is stored under the system key.
  - So erasure misses the person's own corrections, which are the most valuable learning signal (D-0019 R1).
  - Any derived record built from them would never list the person as a contributor.
- **What already works:** scope erasure (master-key deletion) erases everything, derived records included (SI-7).

## Options considered
1. **(a) Contributor-set keys (recommended; owner-approved in principle).**
   - **How:** every derived record is encrypted under a key that belongs to the exact set of subjects (and source
     months) whose content went into it. Destroying any member's keys destroys that key.
   - **Pros:** erasure stays crypto-shredding, with no ledger edits, and the chain stays valid (D-0003).
   - **Cons:** more keys; every derived write must know its sources.
2. **(b) Cascade on erasure:** re-derive without the erased content and rebuild a projection generation.
   - Fails: the old derived copies stay readable in the append-only ledger.
3. **(c) Keep person content out of model prompts and memory.**
   - Fails: it throws away the corrections Nacre learns from.
4. **(d) One random key per derived record, plus a membership list.**
   - Same guarantees as (a), but key count grows with every record and rotation cost with it.
   - (a) groups records by (stream, contributor set, month).

## Decision (proposed)
Option (a), with the owner's design: **one derived key per (stream, set of contributing subjects, month)**.

### 1. Contributors
- **Contributor:** for each source event a derived record is built from, the source event's data-key `subject`
  (a person, or the stream's system subject) together with that key's `month`.
- **Contributor set:** the set of those (subject, month) pairs.
- **Recursion:** a derived record built from other derived records inherits their contributor sets (the union).
- Source months are included so that forgetting a period also reaches derived copies (§4).

### 2. Keys
- **Storage:** a derived key is an ordinary row of `keys.data_keys`.
  - `subject_id = uuid5(NS_DERIVED, stream ‖ canonical sorted contributor set)`.
  - `month` = the month the derived record is written.
  - So the D-0014 master and root rotations rewrap derived keys with no change.
- **Membership:** a new table `keys.key_contributors (key_id, member_subject, member_month, member_is_person)`
  records the set. Same RLS, roles and grants as `keys.data_keys`; `nacre_keyadmin` may SELECT and DELETE.
- **Reuse:** identical sets in the same stream and month share one key (the D-0004 granularity).
- **Size cap:** at most 256 members per set (A-0030). Larger derivations must be split by the caller; append
  refuses otherwise.

### 3. Which records use derived keys

| Derived record | Sources (contributors) |
|---|---|
| Model-call `result` events (D-0022) | the request's `source_event_ids` |
| `lesson_proposed` | its decision and outcome |
| `contradiction_proposed` | decision, outcomes and the targeted belief version |
| Belief / fallback / episode **versions** | every event in the version's edge set (proposals transitively, contradiction proposals, members) |
| Projection keyed MACs | the version's key (already derived from the version event's key) |
| Gate `flag`s, sleep-pass markers | the target outcome |
| **Phase 3: embeddings, recall traces, ContextAssembled** | the content they are computed from: **same rule, binding** |

Every derived write must name its sources. The writer API takes `sources` and derives the key, so no derived write
can fall back to the system key. Enforced by a structure test (A-0031).

### 4. Erasure
- **`erase_person(P)` (D-0014 lifecycle unchanged):** destroys P's own person keys (as now) **and every derived key
  whose contributor set contains P**, in every stream of the org.
- **`forget_period(S, months)`:** destroys stream S's keys for those months (as now) **and every derived key in S
  with a member month in those months**.
- **`delete_scope`:** unchanged; the master key covers everything.
- **Legal holds (D-0014):** a held request is not executed, so neither its own keys nor derived ones are destroyed
  until the hold expires and the request runs.
- **Mixed contributors:** a record built from P and Q is under one key, so **erasing either erases it whole**. Q's
  own source events stay readable under Q's keys.

### 5. Consequences for memory
- **Lost beliefs:** a belief, fallback or episode whose version key is destroyed is **lost**:
  - its content is unreadable;
  - `read_heads` returns it with `content = None`;
  - **it is not recall-eligible** (Phase 2: recall-eligible heads must have readable content; Phase 3 recall inherits
    this).
  - A quorum belief supported by P and Q is lost whole when P is erased (mixed rule).
- **No automatic re-derivation** of a lost belief from surviving evidence: it can re-form only through the normal
  rules from new or surviving proposals. Automatic "rebuild without it" (SPEC "Deletion without editing") is a
  Phase 4 item.
- **Rebuild (D-0017 amendment 2):** destroyed versions are reported `unverifiable`, are never counted as
  differences, and their structural rows are carried into a new generation unchanged.

### 6. Intake change (supersedes D-0004's intake sentence)
- **New rule:** any event with `actor_kind = person` gets **subject = that person**, whatever its event type
  (statement, message, correction, decision, prediction, action, outcome).
- A caller may still name a person subject explicitly (an event *about* a client).
- Everything else stays on the system subject.

## Why this one
- Erasure stays a key operation: no ledger edits, the chain stays valid, and it is provable by tests.
- Grouping by (stream, set, month) keeps the key count bounded (A-0030), and rotation is unchanged.
- Without the intake change, person-authored corrections would escape erasure entirely.

## Consequences
- **Schema:** a new migration for `keys.key_contributors`, plus keyadmin grants.
- **Code changes:**
  - `get_or_create_key` gains a derived-key path;
  - `append_event` gains a `sources` parameter for derived writes;
  - call_model, the stores, the gate and sleep-pass markers pass their sources;
  - `execute_due_shreds` extends erase_person and forget_period;
  - the intake rule changes in `append_event`.
- **Existing derived records:** those written under system keys before this ADR exist only in dev and test
  databases. No production migration is needed; the next gate run uses the new path.
- **Cost:** one extra key lookup per derived write, and more keys per stream-month.

## Tests (Phase 2 gate item 9, rewritten): each must pass
1. **Erasure reaches every derived record that touched the person, and only those.** Erase P, then:
   - unreadable: every model-call record, proposal, belief / fallback / episode version, flag and marker whose
     contributors include P; and their projection MACs are unverifiable;
   - readable (positive control): every derived record without P, and every event of other subjects.
2. **Mixed records are erased whole:** a record built from P and Q is unreadable after erasing P (and, separately,
   after erasing Q). Q's own events stay readable.
3. **Beliefs:**
   - a belief derived from P's content is lost after erasure (content None, not recall-eligible);
   - under an active legal hold it stays readable until the hold expires and the request executes;
   - `rebuild_projection` reports it unverifiable and is otherwise identical.
4. **Forgetting a period** destroys derived keys with any source in those months; others survive.
5. **Intake:** a person's `correction` (and decision, prediction, outcome) is person-subject and is erased with
   them.
6. **Keys:**
   - rotation rewraps derived keys, and records stay readable after rotation;
   - keyadmin can destroy them;
   - `keys.key_contributors` is RLS-scoped like `keys.data_keys`.
7. **No fallback:** every derived write names its sources; there is no fallback to the system key (structure test).
8. **Phase 3 gate (recorded now, binding):** embeddings and recall traces computed from content use contributor-set
   keys and are erased by the same rules.

## How we'd know it was wrong
- Contributor sets or derived keys per stream-month grow too large (A-0030).
- A derived record cannot name its sources (A-0031).
- Erasure latency grows with history.

## Questions for the owner
1. **(D3)** Approve the intake change in §6 (every person-actor event is person-subject), superseding D-0004's
   statement/message-only rule?
2. Approve extending `forget_period` to derived keys through source months (§1, §4)? The design names (stream, set,
   month). Without source months, forgetting month M would miss copies derived in a later month.
3. Confirm that lost beliefs are NOT re-derived automatically in Phase 2 (automatic "rebuild without it" goes to
   Phase 4)?
4. Is a cap of 256 members per contributor set acceptable?
