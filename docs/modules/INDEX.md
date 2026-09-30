# Module index — which file owns which functionality

Paths below are relative to `src/nacre/` (source) and `tests/` (tests).
Status `planned` = not written; waiting on owner approval of D-0002 … D-0005.

## Phase 1 — ledger and scopes (planned, build in this order)

| # | Capability | Functionality | File | Test | Decisions | Assumptions | Status |
|---|---|---|---|---|---|---|---|
| 1 | core | Envelope data type, enums, UUIDv7 id (types only, no logic) | `core/event.py` | `core/test_event.py` | D-0002 | A-0009 | planned |
| 2 | core | Open a Postgres connection / pool (no scope logic) | `core/db.py` | — (covered via scopes tests) | D-0005 | — | planned |
| 3 | schema | Apply ordered SQL migrations with the migrator role | `schema/apply_migrations.py` | `schema/test_apply_migrations.py` | D-0003, D-0005 | — | planned |
| 3a | schema | DDL: `events`, append-only trigger, linkage trigger, roles/grants | `schema/sql/0001_ledger.sql` | (via ledger tests) | D-0002, D-0003 | A-0013 | planned |
| 3b | schema | DDL: `scopes`, `scope_grants`, RLS policies (FORCE) | `schema/sql/0002_scopes_rls.sql` | (via scopes tests) | D-0005 | A-0011 | planned |
| 3c | schema | DDL: `keys` schema, wrapped DEKs, encryption counters, RLS | `schema/sql/0003_keys.sql` | (via keys tests) | D-0004, D-0005 | A-0008, A-0015 | planned |
| 3d | schema | DDL: `checkpoints` table | `schema/sql/0004_checkpoints.sql` | (via ledger tests) | D-0003 | A-0013 | planned |
| 4 | ledger | Canonical byte encoding of an envelope (single source for seal + AAD) | `ledger/encode_envelope.py` | `ledger/test_encode_envelope.py` | D-0002 | — | planned |
| 5 | ledger | Compute an event's seal hash from prev_hash + encoding | `ledger/seal_event.py` | `ledger/test_seal_event.py` | D-0003 | A-0013 | planned |
| 6 | ledger | Detect and redact secrets in a payload before write | `ledger/strip_secrets.py` | `ledger/test_strip_secrets.py` (+ frozen corpus) | D-0002 | A-0010 | planned |
| 7 | scopes | Register a scope/stream (kind, org, parent) as a config event + row | `scopes/register_scope.py` | `scopes/test_register_scope.py` | D-0005 | A-0014 | planned |
| 8 | scopes | Grant/revoke principal access as org-stream config events + projection | `scopes/set_access.py` | `scopes/test_set_access.py` | D-0005 | A-0012 | planned |
| 9 | scopes | Resolve a principal's readable/writable stream sets from grants | `scopes/resolve_access.py` | `scopes/test_resolve_access.py` | D-0005 | A-0012 | planned |
| 10 | scopes | The one door: open a transaction with `SET LOCAL` scope settings | `scopes/open_scoped_session.py` | `scopes/test_open_scoped_session.py` (adversarial RLS suite) | D-0005 | A-0011, A-0012 | planned |
| 11 | keys | Get or create the DEK for (stream, subject, epoch), wrapped by KEK | `keys/get_or_create_key.py` | `keys/test_get_or_create_key.py` | D-0004 | A-0008 | planned |
| 12 | keys | Encrypt a body with AES-256-GCM + AAD; derive MAC sub-keys; count uses | `keys/encrypt_payload.py` | `keys/test_encrypt_payload.py` | D-0002, D-0004 | A-0015 | planned |
| 13 | keys | Decrypt a body, or return `Shredded` if the key is gone | `keys/decrypt_payload.py` | `keys/test_decrypt_payload.py` | D-0004 | — | planned |
| 14 | ledger | Append one event end to end (validate, trust, strip, encrypt, idempotency, lock, sequence, seal, insert) | `ledger/append_event.py` | `ledger/test_append_event.py` (+ concurrency benchmark) | D-0002, D-0003, D-0004, D-0005 | A-0007, A-0009, A-0010, A-0014 | planned |
| 15 | ledger | Store an encrypted attachment by keyed fingerprint (backend: CURRENT.md Q8) | `ledger/store_attachment.py` | `ledger/test_store_attachment.py` | D-0002, D-0004 | — | planned |
| 16 | ledger | Read a stream from seq N, optionally AS_OF(M), decrypting where allowed | `ledger/read_stream.py` | `ledger/test_read_stream.py` | D-0002, D-0005 | — | planned |
| 17 | ledger | Replay one cognitive cycle in causal order | `ledger/replay_cycle.py` | `ledger/test_replay_cycle.py` | D-0002 | — | planned |
| 18 | ledger | Sign and record a checkpoint of stream heads (DB table + external file) | `ledger/write_checkpoint.py` | `ledger/test_write_checkpoint.py` | D-0003 | A-0013 | planned |
| 19 | ledger | Verify a stream's chain and checkpoints without keys (verifier role) | `ledger/verify_chain.py` | `ledger/test_verify_chain.py` (tamper suite) | D-0003 | A-0013 | planned |
| 20 | keys | Shred keys for a stream or a subject; write deletion marker | `keys/shred_keys.py` | `keys/test_shred_keys.py` | D-0004 | A-0008 | planned |
| 21 | scopes | Delete a scope: shred all its keys, mark scope deleted | `scopes/delete_scope.py` | `scopes/test_delete_scope.py` | D-0004, D-0005 | A-0008 | planned |

Notes:
- `keys/` is a new capability folder not in the original plan below; it holds crypto-shredding
  (D-0004), which SPEC places under the ledger. Split out so `ledger/` stays about record/seal/replay.
- `schema/` holds migrations; SQL files are not checked by `check_structure.py` (it scans `*.py`).
- `tests/conftest.py` will provide a real-Postgres fixture (CURRENT.md Q9); it is test
  infrastructure, not a functionality.
- Not in Phase 1 (by SPEC): cross-scope promotion, scope merge for recall, derived-store rebuild
  after shredding, principal authentication, month partitioning (D-0003).

## Planned capability folders (phase in brackets)
ledger [1] · scopes [1] · keys [1] · schema [1] · capture [2] · gate [2] · sleep [2] · stores [2] ·
recall [3] · interface (MCP/SDK) [3] · living [4] · tuner [4] · modes [4] · coding [5] · maps [5] ·
predictor [5] · personal_model [6] · core (tiny shared types only)
