# D-0006: Phase 1 dependencies and test infrastructure

- **Status:** accepted (owner decided, 2026-09-30)
- **Tier:** D2 (dependencies)
- **Date:** 2026-09-30
- **Relies on assumptions:** A-0002, A-0016

## Context
D-0002 to D-0005 need a Postgres driver, AES-GCM/HKDF/Ed25519, a Postgres to test RLS against
(RLS can't be faked), a Python with `uuid.uuid7`, and somewhere to put attachments.

## Options considered
**Postgres driver**
1. **psycopg 3.** Maintained successor to psycopg2, with a native pool and server-side binding.
2. **asyncpg.** Fast, but async-only, which would make the whole core async now.
3. **psycopg2.** Legacy.

**Crypto**
1. **`cryptography`.** Audited, and has AES-GCM, HKDF and Ed25519.
2. **PyNaCl.** Good, but no AES-GCM or HKDF API; it would force XChaCha, which D-0004 did not choose.
3. **Hand-rolled.** Never.

**Test database**
1. **Docker Postgres with a pinned version.** Same everywhere, CI-ready.
2. **Local Homebrew Postgres.** 14.17 on the dev machine; the version drifts per machine.
3. **SQLite.** Has no RLS.

**Python**
1. **3.14, built-in `uuid.uuid7`.**
2. **3.11+ with a hand-written UUIDv7.** More code to own and test.

**Attachments in Phase 1**
1. **Local disk behind a storage interface.** No service needed; S3 slots in later.
2. **S3-compatible (MinIO) from day one.** Another container, and not yet needed.

## Decision
- Runtime: `psycopg[binary]` 3.x, `cryptography`, Python **≥ 3.14** (`requires-python = ">=3.14"`).
- Postgres: **16 or newer** is supported. Tests and dev run **`postgres:17.11`** in Docker,
  pinned by tag, with the digest recorded in the compose file when it is created.
- Attachments: an interface in `core/` plus a local-disk implementation. S3-compatible storage is a later adapter.
- Tests: pytest against the Docker Postgres. There is no mock database for anything touching RLS, triggers or roles.

## Why this one
It is the owner's choice, and each item is the conservative mainstream option that meets D-0002 to D-0005.

## Consequences
- The personal-model phase (6) needs ML libraries on Python 3.14 (A-0016).
- Contributors need Docker to run DB tests. Pure-Python tests (encoding, sealing, crypto) run without it.

## How we'd know it was wrong
- A-0016 fails.
- psycopg's pool misbehaves with `SET LOCAL` (A-0011).
- Attachment volume on local disk becomes a problem before the S3 adapter exists.
