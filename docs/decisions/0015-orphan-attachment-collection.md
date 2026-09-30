# D-0015: Collecting orphan attachment blobs safely

- **Status:** accepted (owner, 2026-09-30, with amendments 1-4 below)
- **Tier:** D2 (changes the BlobStore public interface, adds a DB role and migration, cross-module locking)
- **Date:** 2026-09-30
- **Relies on assumptions:** A-0021, A-0022 (new)

## Amendment history (owner, at acceptance)
1. **Lock order.** The append takes the shared per-blob lock BEFORE the existence check and before writing the
   file, and holds it until commit or rollback. The cleanup takes the exclusive lock and, in the same transaction,
   confirms that no committed event references the blob before deleting it.
2. **Minimum age: 24 hours.** It covers sleeping laptops, debugger pauses and stalled containers, which the lock
   cannot see.
3. **Contract.** `nacre_gc` reads only attachment references. The blob store's contract becomes "delete only
   through the cleanup, only for unreferenced blobs."
4. **Blobs whose keys were destroyed are not deleted now.** Erasure is already final by crypto-shredding. If storage
   ever matters, that becomes a separate D3 ADR, running only after the root rotation that makes the erasure final.

## Context
D-0013 amendment 1 has two parts:
- attachments are written **before** the event commits;
- orphan files are "harmless and garbage-collected", and **an event must never point to a missing file**.

An orphan is a blob that no event references. Two things leave one behind:
- an append that stored its blob and then failed or rolled back;
- a temp file left by a crashed `put_if_absent`.

Collecting orphans is not free. Three things in the code today stand in the way:
1. **`core/blob_store.py` promises "No delete".** Collection needs list and delete operations on the interface, and
   that is a public-interface change.
2. **The dedup race.** `store_attachment` skips writing when the ref already exists (dedup within a key, D-0004).
   Here is how the race goes:
   - the collector sees ref R as unreferenced;
   - an append for the same plaintext and key finds R present, skips the write, and commits an event pointing to R;
   - the collector deletes R.

   That event now points to a missing file, which amendment 1 forbids. An age threshold does not close the race,
   because a dedup hit does not change the blob's age.
3. **Visibility.** Finding references means reading `attachment_ref` across **every** stream. RLS hides other
   streams from `nacre_app`. `nacre_verifier` sees them all, but it is a read-only audit role and should not gain a
   delete duty.

## Options considered
1. **Never collect (keep orphans forever).**
   - Pros: zero risk.
   - Cons: contradicts amendment 1; disk grows with every failed append; crashed temp files stay too.
2. **Age threshold only.** Delete unreferenced blobs older than N hours.
   - Pros: simple; no DB coordination.
   - Cons: does not close race 2, because a dedup reuse does not refresh age. "Touch on reuse" narrows the window
     but leaves a check-then-delete gap, so it is still unsafe under concurrency.
3. **Per-ref advisory lock between append and collector (recommended), plus a minimum age.**
   - How it works:
     - The append transaction takes a **shared** transaction-level advisory lock on the ref before it checks or
       writes the blob, and holds it until commit or rollback.
     - The collector, per candidate, opens a transaction, tries the **exclusive** lock without waiting (skipping
       the ref if the lock is busy), re-checks inside that transaction that no event references the ref, deletes
       the blob, and commits.
   - Why it is correct: an append whose event has committed has its reference visible to the re-check. An append
     still in flight holds the shared lock, so the collector skips the ref. An append that starts after the
     deletion finds the blob absent and writes it again.
   - The minimum age (24 hours, amendment 2) is a second guard: against writers that bypass `store_attachment`, and for
     temp files.
   - Pros: closes the race by construction; cheap.
   - Cons: adds one advisory lock per attachment append; needs a dedicated role.
4. **A pending-attachments table.**
   - How it works: the append writes a "pending" row with the blob, and the collector deletes only blobs with no
     pending row and no event.
   - Pros: explicit.
   - Cons: a new table and migration, and a row that must itself be cleaned up. It still needs locking to close the
     same race, so it does everything option 3 does and more.

## Decision
Option 3.
- **Interface:** `BlobStore` gains `list_refs() -> Iterator[tuple[bytes, float]]` (ref, age in seconds) and
  `delete(ref)`.
  - `delete` is only for orphan collection. Erasure stays key destruction (D-0004), and the header says so.
  - `LocalDiskBlobStore` implements both. It also removes `.tmp` files older than the minimum age.
- **Lock:** `pg_advisory_xact_lock_shared(ledger.attachment_lock_key(ref))` in `store_attachment`, inside the
  append transaction.
  - The key function uses the two-integer advisory lock space, with a namespace distinct from
    `ledger.stream_lock_key`, so attachment locks never collide with stream locks.
- **Role:** migration 0008 adds `nacre_gc`, NOLOGIN plus a test login like the others. It gets:
  - column-level `SELECT (attachment_ref)` on `ledger.events`, with an RLS policy `USING (true)` for that role;
  - nothing else: no key, checkpoint or body access.
- **Functionality #15c:** `ledger/collect_orphan_blobs.py`, `collect_orphan_blobs(conn, store, *, min_age) -> report`.
  It runs one short transaction per candidate and returns counts (deleted, skipped-locked, skipped-young,
  temp-removed).

## Why this one
It is the only option that meets amendment 1's hard rule ("never a missing file") under concurrency, not just
usually. The cost is one advisory lock per attachment. The role keeps collection separate from both the app and the
audit verifier.

## Consequences
- Supersedes the "No delete" note in `core/blob_store.py` and `ledger/local_disk_blob_store.py`. Both headers now
  cite D-0015.
- `store_attachment` changes: it takes the lock, and this is tested.
- Tests:
  - orphan deleted;
  - referenced blob kept;
  - young orphan kept;
  - old temp file removed;
  - **race test:** an append holds the shared lock and dedups onto an orphan while the collector runs; the
    collector skips it, the event commits, and the blob still reads;
  - a blob referenced only by an event whose key was shredded is **kept**, because the event still references it;
  - `nacre_gc` cannot read bodies or keys.
- Blobs of shredded events stay on disk as unreadable ciphertext. Reclaiming them is a separate, later decision
  (see the question below).

## How we'd know it was wrong
- An event read reports a missing blob.
- The shared lock shows up as append latency against A-0007.

## Owner answers
1. Minimum age: 24 hours (amendment 2).
2. Blobs whose key was destroyed: not now (amendment 4).
