# D-0005: Scope isolation (streams, access resolution, database enforcement)

- **Status:** accepted (owner, 2026-09-30, explicit D3 approval, with amendment below)
- **Tier:** D3 (scope isolation, privacy boundary)
- **Date:** 2026-09-30
- **Relies on assumptions:** A-0009, A-0011, A-0012, A-0014
- **Related:** D-0002 (`stream_id` + context ids), D-0003 (verifier role), D-0004 (key table behind the same wall)

## Amendment history (owner-directed, before acceptance)
1. Three requirements stated as hard invariants (see "Non-negotiable invariants" below): the app
   role never owns the tables, FORCE ROW LEVEL SECURITY is on, and a test proves cross-scope reads fail.

## Amendments after acceptance (owner-directed, D3; additive, no accepted text changed)
2. **2026-09-30 — `nacre_checkpointer` role (resolves the D-0003 checkpointer's DB access).**
   The checkpointer process (D-0003) connects as a new role, `nacre_checkpointer`, with:

   | ID | Requirement | How checked |
   |---|---|---|
   | C-1 | It reads **only** `stream_id`, `commit_seq`, `hash` of `ledger.events` (column grants + an RLS policy for this role), never `body_ciphertext` or any other column | Test: selecting each of those three works across all streams; selecting any other column is `permission denied` |
   | C-2 | It may INSERT into `ledger.checkpoints` and read it; no UPDATE/DELETE/TRUNCATE | Test: privileges on `ledger.checkpoints` for the role |
   | C-3 | `ledger.checkpoints` is append-only with the same superuser-proof trigger as `events` (`ledger.reject_mutation`) | Test: UPDATE/DELETE/TRUNCATE rejected even for a superuser |
   | C-4 | The signing key is held only by the checkpointer process, never in the database (no key column; only the public key id and signature are stored) | Test: `ledger.checkpoints` has no private-key column; review of every migration |
   | C-5 | `nacre_verifier` stays read-only on `events` and `checkpoints` | Test: verifier has SELECT only on both |
   | C-6 | `nacre_checkpointer` owns nothing and has no `BYPASSRLS` / superuser (as S-1) and no access to `scopes` or `keys` | Same checks as S-1, plus permission-denied checks |

   Option (b), letting the verifier write checkpoints, was rejected: the verifier would then write
   the evidence it later verifies against.

## Context
SPEC Law 5 and Scopes rule 1: "Isolation is enforced by the database (row-level security per
scope), not by prompts." SPEC names five scope kinds (task, project, user, team/org, agent) and
"one stream per scope". Still open:
- what a stream is;
- how an event that touches several scopes is owned;
- how the database learns who is asking;
- what stops pooled connections leaking one principal's permissions to another request.

## Options considered
**Where isolation is enforced**
1. **App-layer filters only** (`WHERE stream_id IN …`). Violates SPEC Law 5; one missed filter leaks.
2. **Postgres RLS keyed on a transaction-local setting.** The app resolves the principal's
   readable and writable stream sets once, then sets them with `SET LOCAL nacre.read_streams /
   nacre.write_streams` inside the transaction. Policies on `events` and `keys` compare
   `stream_id` against them. One schema, and recall can merge scopes in one query. Trusts the app
   to state the principal honestly (A-0012).
3. **One Postgres role per principal**, RLS on `current_user`. The DB authenticates the
   principal itself. But roles multiply (people × agents), connection pooling breaks,
   and role management becomes a second source of truth outside the ledger.
4. **Schema or database per scope.** Strongest wall. But thousands of schemas, and SPEC's
   cross-scope recall merge (task → project → user → team) becomes a multi-database query.

**Ownership of multi-scope events**
- **a. Exactly one owning stream per event**; the other scope ids are context only (D-0002).
- **b. Write the event into every scope it touches.** Duplicated truth, multiple chains saying
  the same thing, and shredding one copy leaves the others.

**Task scope**
- **x. Tasks are streams.** Literal SPEC. Creates a new gapless sequence, chain and key for every
  short-lived cycle.
- **y. Tasks are a `task_id` inside their owning stream**, with the task readable by that cycle via
  a task-restricted policy. Departs from the literal spec (CURRENT.md Q5).

## Decision
**Option 2 (RLS on transaction-local settings) + a (one owning stream) + y (task inside stream).**

- **Streams exist for** org, team, project, user, agent scopes. Each stream has a
  `scopes` row: `stream_id`, `kind`, `org_id`, `parent_stream_id`, `status`. All ids are opaque UUIDs.
- **Owning stream rule:** the event goes to the narrowest *durable* scope it belongs to. For
  coding agents, per SPEC "project scope = repo scope", that is normally the project stream.
  Cross-scope sharing is only by explicit promotion (later phase), never by writing twice.
- **Access grants** are `config_event`s in the org stream (grant/revoke principal → stream,
  read/append). A `scope_grants` table is a **derived projection** of those events, so Law 1
  holds and the table can be rebuilt from the ledger.
- **One door to the database:** `scopes/open_scoped_session.py` is the only code that opens a
  ledger transaction. Every call goes through these steps:
  1. resolves the principal's grants (`resolve_access`);
  2. begins a transaction;
  3. sets `SET LOCAL` read and write stream arrays;
  4. yields the transaction.

  `SET LOCAL` dies at commit or rollback, so a pooled connection can't carry it into the next request.
- **Policies:** `FORCE ROW LEVEL SECURITY` on `events`, `keys`, `scopes`, `scope_grants`. The app
  role is not the table owner and has no `BYPASSRLS`. SELECT requires
  `stream_id = ANY(current_setting('nacre.read_streams', true)::uuid[])`. When the setting is
  missing, the result is **zero rows**, not an error that could be caught and ignored. INSERT
  requires the write set.
- **Roles:**

  | Role | Can do | Cannot do |
  |---|---|---|
  | `nacre_app` | INSERT/SELECT under RLS | UPDATE/DELETE on the ledger |
  | `nacre_verifier` | SELECT on `events` across all streams, for D-0003 | Any access to `keys` |
  | `nacre_migrator` | DDL | Used only by migrations |

- **Non-negotiable invariants (amendment 1), each with its check:**

  | ID | Invariant | How checked |
  |---|---|---|
  | S-1 | `nacre_app` owns no table, view, sequence or function in the ledger, scopes or keys schemas, and has no `BYPASSRLS` / superuser | Test queries `pg_class.relowner`, `pg_proc.proowner` and `pg_roles.rolbypassrls` / `rolsuper` for `nacre_app` and asserts none |
  | S-2 | `FORCE ROW LEVEL SECURITY` is enabled on `events`, `scopes`, `scope_grants` and every `keys` table | Test asserts `relrowsecurity` and `relforcerowsecurity` are true for each |
  | S-3 | A principal cannot read another scope's events or keys | Adversarial test: principal granted stream A reads stream B, both through the scoped session and with a raw `SELECT` on the `nacre_app` connection, for every scope-kind pair; it must get zero rows. The same holds with the setting missing, after a pooled connection is reused, and inside a savepoint. **Phase 1 gate item** |

- **Defense in depth:** even an RLS mistake on `events` yields ciphertext only, because unwrapping
  keys goes through `keys`, which has its own policy (D-0004).

## Why this one
Option 2 is what SPEC names, and it keeps recall's multi-scope merge a single query. It moves
the security question to one small, testable function (`open_scoped_session`) instead of every
query. Option 3's extra guarantee is against a compromised app process. That is not in our Phase 1
threat model (A-0012), and its role explosion conflicts with Law 1. Option 4 fights the recall
design. One owning stream (a) is what makes per-scope chains (D-0003) and per-scope shredding
(D-0004) coherent. Task-as-stream (x) buys isolation *within a project* that no SPEC scenario needs,
at the cost of a chain and key per cycle.

## Consequences
- Adversarial RLS test suite is a Phase 1 gate item. It covers:
  - every scope-kind pair;
  - a missing setting;
  - a setting leaked across pooled connections;
  - a nested or savepoint transaction;
  - a role accidentally granted ownership.
- The org stream holds the access-control history; bootstrapping the first org and its owner is
  a special migration step, recorded as the org stream's first event.
- Any future code that opens its own connection to ledger tables bypasses the door and is a
  defect. `check_structure.py` may later grep for it.
- Principal authentication (who is calling the MCP/API) is out of Phase 1; Phase 1 takes the
  principal as given (A-0012).

## How we'd know it was wrong
- Any adversarial test returns a row from a non-granted stream.
- Real usage needs isolation between tasks inside one project; revisit task-as-stream.
- A deployment must defend against a compromised app process; revisit option 3 or 4.
- The one-owning-stream rule forces ugly choices for common event kinds (A-0014 walkthrough).
