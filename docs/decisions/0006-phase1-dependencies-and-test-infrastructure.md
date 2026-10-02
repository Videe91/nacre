# D-0006: Phase 1 dependencies and test infrastructure

- **Status:** accepted (owner decided, 2026-09-30)
- **Tier:** D2 (dependencies)
- **Date:** 2026-09-30
- **Relies on assumptions:** A-0002, A-0016

## Amendments after acceptance (owner-directed)
1. **2026-09-30 — `psycopg_pool` adopted** (`psycopg[pool]`) as the production connection setup.
   - **Condition:** scope settings stay transaction-local (D-0005).
   - **Test:** a pooled connection must never carry scope between principals.
   - A-0007 is re-measured with the pool.
2. **2026-10-01: Pillow 12.3.0, test-only** (owner, for D-0027's sealed OCR set I1).
   - An exact pin in the `test` extra; never imported by `src/`.
   - The committed I1 images plus the sha256s in `I1_MANIFEST.json` are the source of truth.
   - Regenerating the images with Pillow is checked only on the pinned platform (Pillow 12.3.0, Darwin-arm64).
3. **2026-10-02: ACCEPTED (owner) on conditions. Pillow becomes a RUNTIME dependency.**
   - **Conditions (binding):**
     - extraction and OCR run in an ISOLATED SUBPROCESS: no network, no database credentials, memory and CPU
       limits, a timeout;
     - `opencv-python-headless` instead of `opencv-python`;
     - ALL transitive dependencies pinned and hash-locked.
   - **Why:** RapidOCR, the OCR engine the owner approved in D-0027, requires Pillow at runtime.
   - **Transitive dependencies it pulls in** (now pinned and hash-locked in `requirements.lock`; check with
     `scripts/check_dependency_lock.py`; `opencv-python` replaced by `opencv-python-headless`): opencv-python, Shapely, pyclipper,
     omegaconf, antlr4-python3-runtime, requests, PyYAML, tqdm, colorlog, six.
   - **Pins:** pypdf 6.19.0, pypdfium2 5.13.0 and rapidocr 3.9.2 are exact-pinned (D-0027 §3–4).
   - **Note:** Pillow now decodes untrusted images on the write path; only the decoder matching the file's magic
     bytes is tried.

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
