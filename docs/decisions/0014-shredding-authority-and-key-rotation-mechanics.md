# D-0014: Shredding authority, key-administration roles, and rotation mechanics

- **Status:** accepted (owner, 2026-09-30, explicit D3 approval, with the safety net below)
- **Tier:** D3 (who may destroy keys, and through which database roles), with D2 parts (audit event formats)
- **Date:** 2026-09-30
- **Relies on assumptions:** A-0008, A-0012
- **Related:** D-0004 (amendments 4, 6, 7: key hierarchy, finality at root rotation, master rotation after data-key
  shreds), D-0005 (roles, RLS, S-1..S-3, C-1..C-6), D-0003 (append path), D-0012 (trust)
- **Blocks:** INDEX #20 shred_keys, #20a rotate_root_key, #20b rotate_master_key, #21 delete_scope

## Amendment history (owner safety net, before acceptance)
1. **Requests, then execution after a grace period.** Destructive actions (delete scope, erase person, forget period)
   are first recorded as **requests**. Key destruction happens only after a **7-day grace period**, cancellable by
   any org admin. Request, cancellation and execution are all ledger events.
2. **Grace period plus root rotation within 30 days.** Root rotation runs **weekly**, plus on demand when a grace
   period ends (this supersedes D-0004 amendment 6's "at least monthly").
3. **Self-erasure executes automatically** after the grace period, unless an org admin places an explicit,
   recorded, **time-limited legal hold**. The hold is a ledger event with an expiry; when it expires, execution
   proceeds.
4. Everything else stands as proposed: the keyadmin role, operator-only root rotation, single-transaction master
   rotation, rotation needs derived from the ledger, and audit events per org.

## Context
D-0004 fixes WHAT shredding and rotation do. No ADR fixes WHO may do them, THROUGH WHICH database role, or HOW
the audit trail is written. Today's roles cannot express the operations:
- `nacre_app` works under per-principal RLS. It cannot delete keys at all (no DELETE grant, by design in
  migration 0003).
- It could never erase one person across **every** stream of an org, or rewrap **every** stream's master key
  during a root rotation, without holding read access to all of them.

## Options considered
1. **Give `nacre_app` DELETE/UPDATE on the key tables under RLS.** One role. But erasure and rotation span
   streams the acting principal may not read, so the policies would need cross-table joins. It also mixes
   ordinary writes with irreversible destruction in one role.
2. **A dedicated `nacre_keyadmin` role, used only by a key-administration service**, on its own connection
   (like the verifier and checkpointer):
   - it can see and change **only** the key tables and the scope registry;
   - it cannot read `ledger.events` bodies;
   - it writes its audit events through the normal append path, as the operating principal.
3. **Do it all as the DB admin (superuser).** Simplest, but no least-privilege boundary and invisible in RLS
   terms. Rejected.

## Decision: option 2

### Role `nacre_keyadmin` (migration, with invariants tested like S-1 and C-1..C-6)
- SELECT, UPDATE(`wrapped_key`, `root_key_version`), DELETE on `keys.stream_master_keys`.
- SELECT, UPDATE(`wrapped_key`), DELETE on `keys.data_keys` (no INSERT: data keys are created only on the write path).
- SELECT on `scopes.scopes` and `scopes.scope_grants`, to check authority and enumerate an org's streams.
- **No** access to `ledger.events` or `ledger.checkpoints` (it never sees ciphertext or bodies).
- Owns nothing; no BYPASSRLS. RLS policies for it cover all rows of the tables it can touch.

### Authority (checked by the service before acting; D3)
| Operation | Who may request it |
|---|---|
| Delete a scope (destroy its master key) | a principal with **append on the org stream** of that scope |
| Erase a person (destroy their data keys in every stream of the org) | a principal with append on the org stream, **or the person themself** |
| Forget a period (destroy data keys of given months in one stream) | a principal with append on the org stream |
| Root rotation | the **installation operator** only (not a scope principal); runs at least monthly and on demand |

### Audit (each step is a ledger event; D-0004 amendment 7)
- Every shred and every master rotation writes `deletion_marker` / `config_event` events, through `append_event`,
  as the requesting principal:
  - **org stream:** the request, the authority check, and each master rotation step;
  - **affected stream:** a marker naming the destroyed `key_id`s, encrypted under that stream's **current system
    key** (never a destroyed key).
- **Root rotation** has no org of its own. **Proposed:** one `config_event` in **each org stream** whose master
  keys were rewrapped, plus the operator's confirmation that the old version's separate backup was destroyed.

### Mechanics
- **Master rotation (#20b):** one transaction per stream:
  1. `UPDATE` the master row to a new master key (new wrapped bytes, current root version);
  2. rewrap every surviving data key of the stream under it;
  3. commit.

  The old master key exists only in pre-rotation backups, wrapped under a root version that the next root
  rotation destroys. A single transaction makes a half-rotated stream impossible (owner condition: never a data
  key wrapped only under a deleted master).
- **"Marked for rotation" is derived from the ledger:** a stream needs master rotation if it has a data-key
  `deletion_marker` newer than its last master-rotation event. No mutable marker table. Exactly one rotation per
  marked stream per cycle.
- **Root rotation (#20a):**
  1. `create_version()`;
  2. rewrap every live master key in batches (resumable: rows still on the old version are simply rewrapped
     again);
  3. verify that no row references the old version;
  4. `destroy_version(old)`;
  5. record the operator's backup-destruction confirmation.

  Master rotations due in this cycle run **before** the root rotation.
- **Scope deletion (#21):** destroy the stream master key (its data keys cascade), mark the scope `deleted`
  (needs an UPDATE(`status`) policy for the service), and write the org-stream event. Derived stores are
  rebuilt later without the stream.
- **A-0008 test (as specified):**
  1. take a `pg_dump` "backup" before shredding;
  2. shred, run master rotation then root rotation, and destroy the old root version;
  3. extract the deleted wrapped keys from the dump and prove neither the data key nor the old master
     unwraps.

  Crash test: kill the rotation job mid-batch and prove that resuming loses no surviving data key.

## Consequences
- A fourth DB role, with its invariants tested.
- The key-administration service is a separate entry point (like the checkpointer). Phase 1 ships it as library
  functions plus tests; exposing it through the interface is Phase 3.
- Authority checks for "the person themself" need principal authentication (A-0012). Until then they are
  asserted, as with trust (D-0012).

## How we'd know it was wrong
- Erasure requests need to cross orgs (a person active in several orgs): that needs installation-level authority.
- The single-transaction master rotation becomes too large for a stream with very many data keys: switch to the
  resumable job the owner allowed.
