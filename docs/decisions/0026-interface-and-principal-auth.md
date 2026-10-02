# D-0026: Interface (MCP server and Python SDK) and principal authentication

- **Status:** accepted (owner, 2026-10-01) with the decisions below
- **Tier:** D3 (who may write as whom; what `trust_basis = verified` proves) and D2 (public interface).
- **Date:** 2026-10-01
- **Relies on assumptions:** A-0006, A-0012 (to be superseded), A-0039, A-0040 (new)

## Owner decisions at acceptance (2026-10-01)
1. **Approved (D3):** per-principal bearer tokens; source ceilings per principal kind; over-claims are rejected,
   never downgraded.
2. **Approved (D3): delegations for `on_behalf_of` are explicit, recorded, time-limited, revocable and scoped.**
   - **Explicit:** created only by the person, or by an operator on the person's recorded instruction, through the
     admin CLI. Never inferred.
   - **Recorded:** every create and revoke is a ledger `config_event` in the org stream (who, which agent, which
     scopes, expiry).
   - **Time-limited:** `expires_at` is required, with no open-ended delegations. The maximum is set in config (proposed:
     90 days, like tokens).
   - **Revocable:** revocation takes effect for the next write; it is checked in the write's transaction.
   - **Scoped:** a delegation lists the scopes it covers; a write in any other scope is rejected.
3. **Approved:** `require_verified` as the default for interface scopes; stdio plus loopback-only HTTP, with OAuth
   later; the `mcp` dependency (exact pin).

## Amendment 1 (owner, 2026-10-01, D3): the corrected claim table. It resolves the erratum below
**The rule:**
- **Agents:**
  - an authenticated agent's **structured** decision, prediction, action and outcome events have **verified
    provenance** (`trust_basis = verified`) but are **never authoritative**;
  - they cannot act as corrections, and cannot trigger single-source promotion (D-0017 amendment 1);
  - they count **only as evidence under the two-decision rule** (quorum ≥ 2 distinct decisions, D-0017);
  - **agent free text is untrusted.**
- **Authoritative corrections come only from:**
  - **authenticated persons acting as reviewers**; or
  - **structured CI / integration results**.

**Encoded as the claims a principal may make**, as (`source`, `authorship`, `actor_kind`). Anything else is
rejected, never downgraded.

| Principal | Event kinds | Allowed claim | Resulting trust | May carry an authoritative `correction` section / `correction` event |
|---|---|---|---|---|
| **agent** | structured decision, prediction, action, outcome | (`chat`, `external`, `agent`) | untrusted, verified | **no**, rejected |
| **agent** | free text (statement, message), relayed tool output | (`chat`, `external`, `agent`); (`tool`, `external`, `agent`) | untrusted, verified | **no**, rejected |
| **person**, no reviewer grant | statement, message, decision | (`chat`, `scope_principal`, `person`) | trusted | **no**, rejected |
| **person with a reviewer grant** on the scope | `correction`; an outcome with a `correction` section | (`review`, `scope_principal`, `person`) | trusted | **yes** |
| **service** (CI / integration connector) | structured outcome / evaluation results only | (its configured `ci` / `review` / `git`, `integration_result`, `system`), `payload_type = structured` | trusted | **yes** (structured results only) |
| **operator** (admin CLI only) | config and admin events | (`system`, `scope_principal`, `system`) | trusted | **no**, rejected |

- **Reviewer grant:** a new `auth.reviewer_grants (person_principal, scope, expires_at, revoked_at,
  config_event_id)`, with the same properties as delegations: explicit, recorded, time-limited, revocable,
  scoped.
- **`on_behalf_of`:** an agent acting for a person is still an agent. It never inherits the person's reviewer
  grant, and its events stay non-authoritative.
- **Tests (binding; part of R3 and gate item 9):** one test per row, both the allowed claim and every forbidden
  neighbour. In addition:
  1. an agent's structured decision is written `verified` + untrusted, and its correction section is rejected;
  2. **two verified agent decisions with the same lesson DO count toward quorum promotion, and one never promotes**
     (single-source promotion needs an authoritative correction);
  3. a person without a reviewer grant cannot write `source = review` or a correction section; with a grant they
     can, and after revocation or expiry they cannot;
  4. `on_behalf_of` a reviewer person does not make an agent's correction authoritative;
  5. the D-0018 / D-0019 authority check (the gate and the sleep-pass admission) agrees with this table on every
     row. That is a cross-check test against `capture/section_authority.py`.

## Amendment 2: database roles for authentication and principal admin. ACCEPTED (owner, 2026-10-02) as proposed
**Owner conditions at acceptance (binding):**
- **Separate roles:** `nacre_auth`, `nacre_principal_admin`, and `nacre_app` read-only on `auth`.
  `nacre_keyadmin` is NOT reused.
- **Tokens are stored only as KEYED hashes:** HMAC-SHA256 under a token-hashing key held outside the database, like
  the root key, so a database copy alone cannot test guesses.
- **Expiry and immediate revocation:** checked on every call.
- **Last-used stamping is throttled:** at most one write per token per interval.

**The proposal as approved:**
**The gap (found before building R1):** D-0026 names the `auth.*` tables but not which database roles may touch
them. Token lookup runs BEFORE any scoped session exists, so it cannot rely on stream RLS.

**Proposal:**
1. **`nacre_auth`** (login role, used only by `authenticate_principal`):
   - may: SELECT on `auth.tokens` and `auth.principals`, and UPDATE of `auth.tokens.last_used_at` only;
   - no access to ledger, keys or scopes.
   - A compromised authenticator can only tell whether a token hash is valid and who it belongs to; it can never
     read content.
2. **`nacre_principal_admin`** (login role, used only by the admin CLI):
   - may: INSERT/UPDATE on `auth.principals`, `auth.tokens` (revoke), `auth.delegations` and
     `auth.reviewer_grants`;
   - every change is ALSO a `config_event` in the org stream, written as the operator through the normal append
     path (as keyadmin does, D-0014);
   - NOINHERIT, no ledger read.
3. **`nacre_app` (the request path):**
   - may: SELECT on `auth.delegations` and `auth.reviewer_grants` (needed to check claims inside the write
     transaction), scoped by org;
   - may not: write anything in `auth`.
4. **Storage:** tokens are stored only as sha256 of the 256-bit secret (D-0026 §2). No role can read a secret,
   because none is stored.

**Questions for the owner (D3):** approve the two new roles and these grants? Or should principal administration
reuse `nacre_keyadmin` (fewer roles, but a broader one)?

## ERRATUM (resolved by amendment 1 above; kept for history)
- **The defect:** the "source ceilings" table in §1 names `source` values `agent` and `person`. D-0012 has no such
  sources.
  - D-0012's sources are `chat | git | ci | review | web | tool | system`.
  - Trust is derived from **source AND `authorship`** (`scope_principal | integration_result | external`).
  - The table also ignores `authorship`.
- **Found by:** the EXP-0004 blind builder, who mapped person-authored events to `chat` + `actor_kind = person`.
- **Why it matters:** under D-0012, `authorship = scope_principal` + `source = chat` is **trusted**. So the real
  ceiling must constrain the triple (`source`, `authorship`, `actor_kind`), and which triples an *agent* principal
  may claim is a D3 trust question.
- **The accepted decision (reject over-claims, never downgrade) is unchanged.** Only the vocabulary of the ceiling
  table is wrong.
- **Proposed correction, NOT applied; it needs the owner (D3):**

  | Kind | May claim (`source`, `authorship`, `actor_kind`) |
  |---|---|
  | person | (`chat`, `scope_principal`, `person`) |
  | agent | (`chat` or `tool`, `external`, `agent`): untrusted, so an agent's own words never ground a lesson; plus (`tool`, `external`, `agent`) for tool output it relays |
  | service | (its configured `ci` / `review` / `git`, `integration_result`, `system`) with structured payloads |
  | operator (admin CLI only) | (`system`, `scope_principal`, `system`) |

  - Open: should an agent's own `decision` / `prediction` / `action` events be `scope_principal` (trusted
    authorship of the agent's *own* acts), while its free text stays `external`?
- **R3 (`check_claims`) is not built until the owner decides.**

## Context
- **Today `trust_basis` is always `asserted`** (D-0012): the app process states the caller, source and actor, and
  nothing checks them (A-0012).
- **Why that has to change:** Phase 3 opens Nacre to real agents. Write-gate authority (D-0018 envelope authority,
  D-0019 R1 "authoritative correction always flags") and D-0023 subjects (`on_behalf_of`) both depend on who wrote
  an event. Until that is verified, an agent could claim `source = review` and plant an "authoritative correction".
- **The SPEC's interface:** an MCP server and a Python SDK.

## Options considered
1. **Bearer tokens per principal, verified by the service; trust_basis is upgraded only when every claimed field is
   within the principal's grants (recommended).**
2. **One shared service token, with the app trusted to name principals.**
   - Rejected: this is A-0012 as it stands. Any agent holding the token can be any principal.
3. **Per-principal Postgres roles** (authentication in the database).
   - Rejected: role sprawl. RLS already scopes data; the gap is authorship, not row access. Connection pooling per
     role defeats the pool.
4. **OAuth 2.1 / OIDC (the MCP HTTP authorization profile) now.**
   - Deferred: the right end state for remote HTTP and organisations with an IdP, but not needed for local stdio
     agents in Phase 3.
   - The token check sits behind one `authenticate()` entry, so OAuth can replace it without touching callers (a
     later ADR).

## Decision (proposed)

### 1. Principals and grants
- **New schema `auth`, RLS by org:**
  - **`auth.principals`:** `principal_id`, `org_id`, `kind ∈ {agent, person, service}`, `display_name`,
    `allowed_sources` (subset of D-0012 sources), `subject_id` for persons (the D-0004 person subject),
    `disabled_at`.
  - **`auth.tokens`:** `token_id`, `principal_id`, `sha256(secret)`, `created_at`, `expires_at` (≤ 90 days),
    `revoked_at`, `last_used_at`. **The secret is never stored.**
  - **`auth.delegations`:** `(delegation_id, agent_principal, person_principal, scopes, created_at, expires_at NOT
    NULL, revoked_at, config_event_id)`. A person allows an agent to act on their behalf. Every create and revoke
    is also a recorded `config_event` (owner decision 2).
- **Scope access** stays `scope_grants` (D-0005), keyed by principal.

**Source ceilings per principal kind:**

| Kind | May claim `source` | May claim `actor_kind` |
|---|---|---|
| agent | `agent`, `tool` | `agent` |
| person | `person` | `person` |
| service (CI, review, git connector) | its configured subset of `ci`, `review`, `git` | `system` |

### 2. Token format
- **Format:** `nacre_pat_<token_id base32>_<secret 256-bit base62>_<crc32>`.
- **Why a fixed prefix and checksum:**
  - Nacre's own scanner (D-0007, an additive rule) and gitleaks-style tools recognise a leaked token;
  - the checksum rejects typos before any database lookup.
- **Shown once at creation (admin CLI).** Never logged, never in an event, never in a trace.
- **Tests build token-shaped strings at runtime** (standing rule).

### 3. Authentication and `trust_basis = verified`
- **Every call resolves a token to a principal** (constant-time compare; expired, revoked or disabled → rejected).
- **The claims on each write are checked against the principal:**
  - `actor` is the principal;
  - `source` is within `allowed_sources`;
  - `actor_kind` matches the kind;
  - `on_behalf_of = P` needs a live delegation from P covering the scope;
  - the scope is granted.
- **Any claim outside the grants → the write is REJECTED** with a clear error. It is never silently downgraded to
  `asserted`.
- **When all claims pass:** the envelope gets `trust_basis = verified`, and `actor` / `on_behalf_of` are the
  verified ids (D-0012).
- **In-process SDK writes without a token keep `asserted`** (unit tests, the eval harness). The write gate's
  authority rules are unchanged: they read `trust` and `source`. `trust_basis` is recorded, and a later ADR can make
  the gate require `verified` for authority.
  - **Proposed now:** in a scope marked `require_verified`, `asserted` writes are rejected (default on for scopes
    created through the interface).

### 4. MCP server
- **Transport:**
  - **stdio** (local coding agents; the token comes from the agent's MCP config env `NACRE_TOKEN`);
  - **Streamable HTTP**, loopback-only in Phase 3. A non-loopback bind requires TLS and is refused otherwise.
- **Tools (each a thin call to one public entry):**
  - `recall_context` → D-0025; returns `frame_id` plus the rendered memory section;
  - `record_decision`, `record_prediction`, `record_action`, `record_outcome`, `record_correction`,
    `record_statement` → capture (D-0018);
  - `get_frame(frame_id)` → the frame, if the principal can still read every part.
- **Not exposed over MCP:** erasure, key administration, principal and token management, scope creation. These stay
  in the admin CLI, run by an operator.
- **Limits:** request body ≤ 1 MiB (the D-0022 large-body route is not built); per-principal rate limit; attachments
  go through binary scanning (D-0027) before storage.
- **Errors:** typed and stable codes (`unauthenticated`, `forbidden_claim`, `scope_not_granted`, `rate_limited`,
  `attachment_rejected`, …). They never echo secret material.

### 5. Python SDK
- **`nacre.sdk.Client(url | stdio command, token)`** with the same operations as the MCP tools, typed. It is a thin
  client: no recall logic on the client side.
- **An in-process mode** (`Client.local(pool)`) for tests and the eval harness. It writes `asserted`.

## Why this one
- **It closes A-0012's gap at the point it matters:** authorship of authoritative content.
- **It is simple enough for local agents now,** and replaceable by OAuth later behind one entry.
- **Rejecting over-claims is safer than downgrading.** A downgrade would hide an attack attempt in normal-looking
  data.

## Consequences
- **New:**
  - migration `auth.*`;
  - `interface/authenticate_principal.py`, `interface/check_claims.py`;
  - `interface/mcp_server.py` (transport and dispatch only), `interface/render_frame.py` (D-0025 §7);
  - `sdk/client.py`;
  - `interface/admin_cli.py` (principals, tokens, delegations, scopes, erasure requests);
  - a D-0007 detection rule for `nacre_pat_`.
- **Dependencies (D2):** the official MCP Python SDK (`mcp`), pinned by exact version.
- **Supersedes A-0012** once accepted (Phase 1 threat model). A security review names the attacker classes:
  - a malicious agent with a valid token;
  - a stolen token;
  - a compromised connector;
  - a local user on the same machine (stdio).

## Tests (Phase 3 gate items)
1. **Claims:**
   - every claim outside the grants is rejected: source above the ceiling, wrong actor_kind, an ungranted scope,
     `on_behalf_of` without a delegation, an expired delegation;
   - valid claims are written `verified`.
2. **Tokens:**
   - expired, revoked or disabled → rejected;
   - the secret is never in the database, logs, events or traces (scan everything after a test run);
   - a typo fails the checksum.
3. **An agent cannot plant an authoritative correction:** `source = review` from an agent principal is rejected,
   and the gate never sees it.
4. **The MCP surface exposes no admin or erasure tool** (introspection test).
5. **A non-loopback HTTP bind without TLS refuses to start.**

## How we'd know it was wrong
- Real agents need claims this model cannot express (for example, one agent serving many persons without a
  delegation per person).
- A security review finds a path to `verified` without a token check.

## Questions for the owner
1. **(D3)** Approve per-principal bearer tokens, source ceilings per principal kind, and **reject (not downgrade)**
   over-claims.
2. **(D3)** Approve delegations as the only way to write `on_behalf_of` a person.
3. Approve `require_verified` as the default for scopes created through the interface.
4. Approve stdio plus loopback-only HTTP in Phase 3, with OAuth 2.1 and remote HTTP as a later ADR.
5. Approve adding the `mcp` SDK dependency (exact pin).
