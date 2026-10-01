# Module index — which file owns which functionality

Paths are repo-relative (checked by `scripts/check_structure.py`).
Status: `planned` = not written; `done` = written, tested, gate-relevant tests passing.
D-0002 … D-0006 accepted 2026-09-30.

## Phase 1 — ledger and scopes

Build order note (2026-09-30): #7 and #8 write config events into the org stream, so they need #14
`append_event` (and #6, #11–#13). They are built after #14. Order now: 9, 10, 6, 11a, 11, 12, 13, 14, 7, 8,
then 15 onward.

| # | Capability | Functionality | File | Test | Decisions | Assumptions | Status |
|---|---|---|---|---|---|---|---|
| 1 | core | Envelope data type, enums, UUIDv7 id (types only, no logic) | `src/nacre/core/event.py` | `tests/core/test_event.py` | D-0002, D-0006 | A-0009 | done |
| 2 | core | Open one Postgres connection per DB role: explicit READ COMMITTED, UTC (no pool, no scope logic) | `src/nacre/core/db.py` | `tests/core/test_db.py` | D-0003, D-0005, D-0006 | A-0011 | done |
| 2a | core | Blob storage interface (Protocol only: write-once put_if_absent / get / exists by 32-byte ref; list_refs / delete / remove_stale_temp for orphan collection only) | `src/nacre/core/blob_store.py` | — (via 15a, 15c) | D-0004, D-0006, D-0015 | — | done |
| 2b | core | Root-key provider interface (Protocol only: wrap / unwrap a stream master key bound to a context, current version, create/destroy version) | `src/nacre/core/root_key_provider.py` | — (via 11a) | D-0004 | A-0008 | done |
| 2c | core | Deterministic CBOR encoder, strict subset (no floats) | `src/nacre/core/encode_cbor.py` | `tests/core/test_encode_cbor.py` (RFC 8949 App. A subset vectors, shortest-form boundaries, frozen vectors, cbor2 cross-check, hypothesis) | D-0008 | — | done |
| 2d | core | Strict deterministic CBOR decoder: rejects non-canonical / out-of-subset input | `src/nacre/core/decode_cbor.py` | `tests/core/test_decode_cbor.py` (35 rejection vectors, hypothesis round-trips + fuzz, cbor2 cross-check) | D-0008 | — | done |
| 3 | schema | Apply ordered SQL migrations exactly once (checksummed, locked, one transaction each; objects owned by nacre_migrator) | `src/nacre/schema/apply_migrations.py` | `tests/schema/test_apply_migrations.py` | D-0003, D-0005, D-0006 | A-0002 | done |
| 3a | schema | DDL: `events`, append-only trigger, linkage trigger, roles/grants | `src/nacre/schema/sql/0001_ledger.sql` | `tests/schema/test_0001_ledger.py` | D-0002, D-0003, D-0005 | A-0013 | done |
| 3b | schema | DDL: `scopes`, `scope_grants`, RLS policies (FORCE) | `src/nacre/schema/sql/0002_scopes_rls.sql` | `tests/schema/test_0002_scopes_rls.py` | D-0005 | A-0011 | done |
| 3c | schema | DDL: `keys` schema: stream master keys, data keys (stream, subject, month), encryption counters, RLS | `src/nacre/schema/sql/0003_keys.sql` | `tests/schema/test_0003_keys.py` | D-0004, D-0005 | A-0008, A-0015 | done |
| 3d | schema | DDL: `nacre_checkpointer` role (column-granted head reads), append-only `checkpoints` table | `src/nacre/schema/sql/0004_checkpoints.sql` | `tests/schema/test_0004_checkpoints.py` | D-0003, D-0005 | A-0013 | done |
| 3e | schema | DDL: envelope v2 — `trust_basis` column, version/trust_basis check (fix-forward of 0001's version check) | `src/nacre/schema/sql/0005_envelope_v2_trust_basis.sql` | `tests/schema/test_0001_ledger.py` (v2 test) | D-0002, D-0012 | — | done |
| 3f | schema | DDL: scope org integrity — composite FKs (grant stream in same org; parent in same org) | `src/nacre/schema/sql/0006_scope_org_integrity.sql` | `tests/schema/test_0002_scopes_rls.py` (0006 test) | D-0005 | — | done |
| 3g | schema | DDL: `nacre_keyadmin` role (keys + scope registry only, never events; NOINHERIT member of nacre_app for atomic audit) | `src/nacre/schema/sql/0007_keyadmin_role.sql` | `tests/schema/test_0007_keyadmin_role.py` | D-0014 | — | done |
| 3h | schema | DDL: `nacre_gc` role (reads only `ledger.events.attachment_ref`) and the two-integer per-attachment advisory-lock key | `src/nacre/schema/sql/0008_orphan_collection.sql` | `tests/schema/test_0008_orphan_collection.py` | D-0015 | A-0022 | done |
| 4 | ledger | Canonical byte encoding of an envelope (single source for seal + AAD) | `src/nacre/ledger/encode_envelope.py` | `tests/ledger/test_encode_envelope.py` | D-0002, D-0003, D-0008 | — | done |
| 5 | ledger | Compute an event's seal hash from prev_hash + encoding | `src/nacre/ledger/seal_event.py` | `tests/ledger/test_seal_event.py` | D-0003 | A-0013 | done |
| 6 | ledger | Detect and redact secrets before write: vendored gitleaks rules + entropy check | `src/nacre/ledger/strip_secrets.py` (+ data `src/nacre/ledger/data/gitleaks-v8.30.1.toml`, license beside it; `src/nacre/ledger/data/nacre-rules-v1.toml`, D-0011) | `tests/ledger/test_strip_secrets.py` (+ generated corpus `tests/ledger/secret_corpus/`: framework, generic category, provider generators and negatives done; self-tests `tests/ledger/test_secret_corpus.py`) | D-0002, D-0007, D-0008, D-0009 | A-0010, A-0018 | done: H3 (official) 100% per-character in every group, FP 0/400 |
| 7 | scopes | Register a scope/stream (team/project/user/agent) as a config event in the org stream + registry row, atomically; caller-chosen stream id so retries are exact | `src/nacre/scopes/register_scope.py` | `tests/scopes/test_register_scope.py` | D-0005, D-0012 | A-0014 | done |
| 7a | scopes | Bootstrap an org (admin only): first org-stream event, org row, owner grant, one transaction | `src/nacre/scopes/bootstrap_org.py` | `tests/scopes/test_bootstrap_org.py` | D-0005, D-0012 | A-0012 | done |
| 8 | scopes | Grant/change/revoke principal access as an org-stream config event + insert-only grant row (latest wins; append ⇒ read; same-org FK) | `src/nacre/scopes/set_access.py` | `tests/scopes/test_set_access.py` | D-0005, D-0012 | A-0012 | done |
| 9 | scopes | Resolve a principal's readable/writable stream sets from grants (latest grant wins, no inheritance) | `src/nacre/scopes/resolve_access.py` | `tests/scopes/test_resolve_access.py` | D-0005 | A-0012 | done |
| 10 | scopes | The one door: open a transaction with `SET LOCAL` scope settings; refuses superuser/BYPASSRLS roles, open transactions, non-READ-COMMITTED | `src/nacre/scopes/open_scoped_session.py` | `tests/scopes/test_open_scoped_session.py` (adversarial RLS suite: all 25 scope-kind pairs, reuse, rollback, revoke) | D-0003, D-0005 | A-0011, A-0012 | done |
| 11a | keys | Local-file implementation of the root-key provider (0600 files, version-and-context-bound wraps, create/destroy versions) | `src/nacre/keys/local_file_root_key.py` | `tests/keys/test_local_file_root_key.py` | D-0004 | A-0008 | done |
| 11 | keys | Resolve data keys: get-or-create for (stream, subject, month) to write, load-by-id to read (None = shredded); master key created on first use; race-safe | `src/nacre/keys/get_or_create_key.py` | `tests/keys/test_get_or_create_key.py` | D-0004, D-0005 | A-0008 | done |
| 12 | keys | Encrypt a body with AES-256-GCM + AAD; derive MAC sub-keys; count uses | `src/nacre/keys/encrypt_payload.py` | `tests/keys/test_encrypt_payload.py` | D-0002, D-0004, D-0008 | A-0015 | done |
| 13 | keys | Decrypt a body, or return `Shredded` if the key is gone; refuses unreadable streams and any header/AEAD/CBOR fault | `src/nacre/keys/decrypt_payload.py` | `tests/keys/test_decrypt_payload.py` | D-0004, D-0005, D-0008 | — | done |
| 14 | ledger | Append one event end to end (validate, trust by source+author, strip, idempotency with principal-bound MAC, lock, sequence, encrypt, seal, insert) | `src/nacre/ledger/append_event.py` | `tests/ledger/test_append_event.py` (+ A-0007 benchmark `scripts/bench_append_throughput.py`) | D-0002, D-0003, D-0004, D-0005, D-0007, D-0008, D-0012 | A-0007, A-0009, A-0010, A-0014 | done (A-0007 measured) |
| 14a | ledger | Validate an append request and derive its trust (split out of append_event, owner 2026-09-30) | `src/nacre/ledger/validate_append.py` | `tests/ledger/test_validate_append.py` | D-0002, D-0012, D-0013 | A-0009, A-0012 | done |
| 15 | ledger | Encrypt + store one attachment under the event's key (keyed-fingerprint ref, dedup within key, 16 MiB limit), before the event commits | `src/nacre/ledger/store_attachment.py` | `tests/ledger/test_read_attachment.py` (+ lock: `tests/ledger/test_collect_orphan_blobs.py`) | D-0002, D-0004, D-0008, D-0013, D-0015 | A-0015, A-0022 | done |
| 15b | ledger | Read an attachment, verifying blob sha256 and plaintext ref on every read; Shredded if the key is gone | `src/nacre/ledger/read_attachment.py` | `tests/ledger/test_read_attachment.py` | D-0004, D-0005, D-0013 | — | done |
| 15c | ledger | Garbage-collect orphan attachment blobs (no event points to them) | `src/nacre/ledger/collect_orphan_blobs.py` | `tests/ledger/test_collect_orphan_blobs.py` | D-0013, D-0015 | A-0022 | done |
| 15a | ledger | Local-disk BlobStore: sharded layout, 0600, write-once via temp + hard link | `src/nacre/ledger/local_disk_blob_store.py` | `tests/ledger/test_local_disk_blob_store.py` (+ `test_collect_orphan_blobs.py`) | D-0006, D-0013, D-0015 | — | done |
| 16 | ledger | Read a stream from seq N, optionally AS_OF(M) (refused beyond the head), decrypting where the key exists; head() | `src/nacre/ledger/read_stream.py` | `tests/ledger/test_read_stream.py` | D-0002, D-0003, D-0004, D-0005 | A-0012 | done |
| 17 | ledger | Replay one cognitive cycle of one stream in commit order, causal links verified | `src/nacre/ledger/replay_cycle.py` | `tests/ledger/test_replay_cycle.py` | D-0002, D-0003 | — | done |
| 18 | ledger | Checkpointer: sign due stream heads (1,000 events or hourly) into ledger.checkpoints + JSON Lines witness; Ed25519 key file held only by it | `src/nacre/ledger/write_checkpoint.py` | `tests/ledger/test_write_checkpoint.py` | D-0003, D-0005, D-0013 | A-0013, A-0020 | done |
| 19 | ledger | Verify every chain (gapless, linkage, recomputed seals) and every witnessed checkpoint (trusted keys only), reconcile DB vs witness; no keys needed | `src/nacre/ledger/verify_chain.py` | `tests/ledger/test_verify_chain.py` (tamper suite) | D-0002, D-0003, D-0005, D-0013 | A-0013, A-0020 | done |
| 20 | keys | Destructive key requests: request / cancel / legal hold, 7-day grace, state derived from the org stream | `src/nacre/keys/manage_shred_requests.py` | `tests/keys/test_shred_requests.py` | D-0004, D-0014 | A-0012 | done |
| 20c | keys | Execute due destructive requests: destroy keys + record (deletion markers, shred_executed) in one transaction | `src/nacre/keys/execute_due_shreds.py` | `tests/keys/test_shred_requests.py` | D-0004, D-0014 | A-0008 | done |
| 20d | keys | Key-admin transaction: keyadmin key ops + app-mode audit appends, one atomic commit | `src/nacre/keys/keyadmin_session.py` | `tests/keys/test_keyadmin_session.py` (+ `test_shred_requests.py`) | D-0005, D-0014 | A-0012 | done |
| 20a | keys | Rotate the root key (≥ monthly + on demand): rewrap surviving master keys, destroy the old root version, record the required backup-destruction step | `src/nacre/keys/rotate_root_key.py` | `tests/keys/test_rotation_finality.py` (shared with #20b: A-0008 needs both rotations in one scenario) | D-0004, D-0014 | A-0008 | done |
| 20b | keys | Rotate a stream's master key after data-key shredding: new master → rewrap surviving data keys → delete old; resumable, one per stream per cycle; org-stream events; cache invalidation | `src/nacre/keys/rotate_master_key.py` | `tests/keys/test_rotation_finality.py` (+ crash/resume tests) | D-0004, D-0014 | A-0008 | done |
| 21 | scopes | Delete a scope — folded into #20/#20c (request kind `delete_scope`: master key destroyed after grace, status 'deleted') | (no separate file) | `tests/keys/test_shred_requests.py` | D-0004, D-0005, D-0014 | A-0008 | done (folded) |

Notes:
- `keys/` is a new capability folder not in the original plan below; it holds crypto-shredding
  (D-0004), which SPEC places under the ledger. Split out so `ledger/` stays about record/seal/replay.
- `schema/` holds migrations; SQL files are not checked by `check_structure.py` (it scans `*.py`).
- Test infrastructure (not functionalities): `docker-compose.yml` (`postgres:17.11`, digest-pinned,
  port 54329, tmpfs data) and `tests/conftest.py` (`pg_dsn` fixture; fails, never skips, if the DB is down).
- Not in Phase 1 (by SPEC): cross-scope promotion, scope merge for recall, derived-store rebuild
  after shredding, principal authentication, month partitioning (D-0003).

## Phase 2 — interpretation plane (rebuild, not port; D-0016 … D-0022 accepted 2026-10-01; bar = EXP-0003)

| # | Capability | Functionality | File | Test | Decisions | Assumptions | Status |
|---|---|---|---|---|---|---|---|
| P1 | eval | Verify the frozen MNEXA suite: every copied file and every hash-only entry against MANIFEST.json | `src/nacre/eval/verify_frozen_suite.py` | `tests/eval/test_verify_frozen_suite.py` | D-0016 | — | done |
| P2 | eval | Port MNEXA `grade_text` and the regrade profiles: grade a decision against a frozen task's grader spec (deterministic) | `src/nacre/eval/grade_decision.py` | `tests/eval/test_grade_decision.py` (L1: 003–016 exact) | D-0016 | — | done |
| P3 | eval | Map a frozen MNEXA family to Nacre capture events: decision, plus an outcome whose sections follow the EXP-0003 role-to-section table (markers removed) | `src/nacre/eval/load_mnexa_family.py` | `tests/eval/test_load_mnexa_family.py` | D-0016, D-0018 | A-0026 | planned |
| P5 | eval | EXP-0003 run: Nacre arm plus same-run no-memory control, k = 3, graded by P2, separate discarded safety-challenge check, the bar applied, report written (owner-run) | `src/nacre/eval/run_phase2_gate.py` | `tests/eval/test_run_phase2_gate.py` (recorded mode) | D-0016 | A-0023, A-0024 | planned |
| P6 | core | Model request/response types and the ModelProvider protocol (types only) | `src/nacre/core/model_provider.py` | `—` (via P9) | D-0021 | — | done |
| P7 | models | OpenAI Responses adapter (SDK retries off; the only file importing `openai`) | `src/nacre/models/openai_responses_provider.py` | `tests/models/test_openai_responses_provider.py` (fake SDK client) | D-0021 | A-0024 | done |
| P8 | models | Anthropic Messages adapter (the only file importing `anthropic`) | `src/nacre/models/anthropic_messages_provider.py` | `tests/models/test_anthropic_messages_provider.py` (fake SDK client) | D-0021 | — | planned |
| P9 | models | Call a model end to end: provider-policy check, one-scope check, canonicalise and hash, bounded retries with recorded timeout/retry settings, cost from the price table (fail closed), record each attempt as a `result` event | `src/nacre/models/call_model.py` | `tests/models/test_call_model.py` | D-0021, D-0022 | A-0025 | done |
| P9b | models | Record an org's model-provider allow-list (org admin; dated pins only) and resolve the policy in force (default deny) | `src/nacre/models/set_model_policy.py` | `tests/models/test_set_model_policy.py` | D-0021, D-0005 | A-0012 | done |
| P10 | models | Replay provider: answer only from recorded `result` events with the same request hash, in recorded order; RecordingMiss otherwise | `src/nacre/models/recorded_provider.py` | `tests/models/test_recorded_provider.py` | D-0022 | A-0025 | done |
| P11 | models | Load and verify model-call fixture files into a test ledger | `src/nacre/models/load_model_call_fixtures.py` | `tests/models/test_load_model_call_fixtures.py` | D-0022 | — | planned |
| P12 | schema | DDL: `interp` projection (versions, edges, heads view), RLS by stream, append-only | `src/nacre/schema/sql/0009_interpretation_plane.sql` | `tests/schema/test_0009_interpretation_plane.py` | D-0017 | — | done |
| P13 | capture | Record a decision | `src/nacre/capture/record_decision.py` | `tests/capture/test_record_decision.py` | D-0018 | — | done |
| P14 | capture | Record a prediction | `src/nacre/capture/record_prediction.py` | `tests/capture/test_record_prediction.py` | D-0018 | — | done |
| P15 | capture | Record a dispatched action (`execution_of`) | `src/nacre/capture/record_action.py` | `tests/capture/test_record_action.py` | D-0018 | — | done |
| P16 | capture | Record an outcome: typed sections, `outcome_for`, optional `evaluates_prediction`; absence is never failure | `src/nacre/capture/record_outcome.py` | `tests/capture/test_record_outcome.py` | D-0018 | A-0026 | done |
| P17 | capture | Record a correction (`correction_of`) | `src/nacre/capture/record_correction.py` | `tests/capture/test_record_correction.py` | D-0018 | — | done |
| P18 | capture | Validate typed refs: known rel, backward-only, same stream, no self-reference | `src/nacre/capture/validate_refs.py` | `tests/capture/test_validate_refs.py` | D-0018 | — | done |
| P19 | capture | Decide section authority from the envelope (the D3 rule) | `src/nacre/capture/section_authority.py` | `tests/capture/test_section_authority.py` | D-0018 | A-0026 | done |
| P20 | gate | Score one event: surprise, stakes, statement | `src/nacre/gate/score_event.py` | `tests/gate/test_score_event.py` | D-0019 | A-0028 | planned |
| P21 | gate | Flag events above the mode threshold (idempotent `flag` memory events) | `src/nacre/gate/flag_events.py` | `tests/gate/test_flag_events.py` (+ 100% flag recall on 003–016) | D-0019 | A-0028 | planned |
| P22 | sleep | Build the evidence bundle for one flagged episode (decision as history; sections with authority) | `src/nacre/sleep/build_evidence_bundle.py` | `tests/sleep/test_build_evidence_bundle.py` | D-0020 | — | planned |
| P23 | sleep | Proposer seat: structured-proposition proposal (Nacre's own prompt, frozen by hash in the file) | `src/nacre/sleep/propose_propositions.py` | `tests/sleep/test_propose_propositions.py` (recorded) | D-0020, D-0021 | A-0025 | planned |
| P24 | sleep | Repair seat: structure repair (Nacre's own prompt, frozen by hash in the file) | `src/nacre/sleep/repair_structure.py` | `tests/sleep/test_repair_structure.py` (recorded) | D-0020, D-0021 | A-0025 | planned |
| P25 | sleep | Support-first admission: exact spans, authority, structure, fallback, closed world (MNEXA 006/007/008/014) | `src/nacre/sleep/admit_propositions.py` | `tests/sleep/test_admit_propositions.py` (+ L2) | D-0020, D-0018 | A-0026 | planned |
| P26 | sleep | Run the sleep pass for one scope: flags since the last pass, per-episode transactions, resumable | `src/nacre/sleep/run_sleep_pass.py` | `tests/sleep/test_run_sleep_pass.py` (recorded; crash/resume) | D-0020 | A-0025 | planned |
| P27 | stores | Record a lesson proposal (pinned span edges) | `src/nacre/stores/propose_lesson.py` | `tests/stores/test_propose_lesson.py` | D-0017 | — | planned |
| P28 | stores | Promote on quorum, and version on new independent support (never re-activates; retry returns head) | `src/nacre/stores/promote_if_supported.py` | `tests/stores/test_promote_if_supported.py` (MNEXA 36–37 translated + deviations) | D-0017 | A-0027 | planned |
| P29 | stores | Record a contradiction proposal pinned to one belief version | `src/nacre/stores/propose_contradiction.py` | `tests/stores/test_propose_contradiction.py` | D-0017 | — | planned |
| P30 | stores | Contest a belief on quorum (no-op on contested or superseded) | `src/nacre/stores/contest_belief.py` | `tests/stores/test_contest_belief.py` (MNEXA 38 translated + deviations) | D-0017 | — | planned |
| P31 | stores | Supersede a contested belief (shared counter-decisions) | `src/nacre/stores/supersede_belief.py` | `tests/stores/test_supersede_belief.py` (MNEXA 39 translated) | D-0017 | — | planned |
| P32 | stores | Read interpretation heads as-of N with status (content decrypted from the ledger) | `src/nacre/stores/read_heads.py` | `tests/stores/test_read_heads.py` | D-0017 | — | planned |
| P33 | stores | Rebuild a scope's `interp` projection from its memory events (must be identical) | `src/nacre/stores/rebuild_projection.py` | `tests/stores/test_rebuild_projection.py` | D-0017 | — | planned |
| P34 | stores | Propose and commit an episode version (MNEXA ADR-0009: admissibility validation, runtime commits) | `src/nacre/stores/commit_episode.py` | `tests/stores/test_commit_episode.py` (Q-1…Q-15) | D-0017 | — | planned |

Build order: P1–P2 (instrument first; L1 before any mechanism code) → P6, P9–P11 (+ price table `src/nacre/models/data/prices.json`) → P12 → P13–P19 → P20–P21 → P27–P34 (promotion waits on the single-episode question) → P22–P26 → P3 → P5 (EXP-0003, owner-run).

## Planned capability folders (phase in brackets)
ledger [1] · scopes [1] · keys [1] · schema [1] · capture [2] · gate [2] · sleep [2] · stores [2] · models [2] · eval [2] ·
recall [3] · interface (MCP/SDK) [3] · living [4] · tuner [4] · modes [4] · coding [5] · maps [5] ·
predictor [5] · personal_model [6] · core (tiny shared types only)
