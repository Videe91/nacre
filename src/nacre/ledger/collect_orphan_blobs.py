"""
Functionality: Delete attachment blobs that no committed event references, safely under concurrent appends.
Owns: the nacre_gc connection check, the 24-hour minimum age, the exclusive per-ref lock, the in-transaction
  "no committed reference" check, the deletion, stale temp-file removal, and the report.
Public entry: collect_orphan_blobs(), CollectionReport, MIN_AGE
Decisions: D-0013, D-0015
Assumptions: A-0022
Notes: D-0015 with owner amendments:
  - Runs on a nacre_gc login, which can read only ledger.events.attachment_ref (migration 0008).
  - Per candidate, in ONE transaction: try the EXCLUSIVE advisory lock without waiting (busy -> skip; an append
    holds the shared lock from before its existence check until it commits), then check that no committed event
    references the ref, then delete. Under READ COMMITTED the check's snapshot is taken after the lock is granted,
    so an append that committed and released the lock is visible.
  - Blobs younger than MIN_AGE (24 h) are never touched: this covers writers the lock cannot see (a suspended
    laptop, a paused debugger, a stalled container).
  - Blobs of events whose key was destroyed are still referenced, so they are kept (amendment 4).
  - D1: one pass first reads the set of referenced refs to skip obvious keeps cheaply. It is only a filter;
    the decision to delete is always the locked, in-transaction check (tested with an event committed after the
    pre-filter). Removing the pre-filter changes no outcome, only cost: an equivalent mutant.
"""
from dataclasses import dataclass
from datetime import timedelta

import psycopg
from psycopg.pq import TransactionStatus

from nacre.core.blob_store import BlobStore

MIN_AGE = timedelta(hours=24)


class CollectionError(RuntimeError):
    """The collector cannot run safely on this connection or with these settings."""


@dataclass
class CollectionReport:
    deleted: int = 0
    kept_referenced: int = 0
    skipped_young: int = 0
    skipped_locked: int = 0
    temp_removed: int = 0


def collect_orphan_blobs(conn: psycopg.Connection, store: BlobStore, *, min_age: timedelta = MIN_AGE) -> CollectionReport:
    """Delete every blob older than `min_age` that no committed event references."""
    if min_age < MIN_AGE:
        raise CollectionError(f"min_age may not be below {MIN_AGE} (D-0015 amendment 2)")
    if conn.info.transaction_status != TransactionStatus.IDLE:
        raise CollectionError("start outside a transaction")
    user = conn.execute("SELECT session_user").fetchone()[0]
    conn.rollback()
    if user != "nacre_gc":
        raise CollectionError(f"orphan collection needs a nacre_gc login, not {user}")
    report, floor = CollectionReport(), min_age.total_seconds()
    referenced = {bytes(r) for (r,) in conn.execute(
        "SELECT DISTINCT attachment_ref FROM ledger.events WHERE attachment_ref IS NOT NULL")}
    conn.rollback()
    for ref, age in list(store.list_refs()):
        if age < floor:
            report.skipped_young += 1
        elif ref in referenced:
            report.kept_referenced += 1
        else:
            _collect_one(conn, store, ref, report)
    report.temp_removed = store.remove_stale_temp(floor)
    return report


def _collect_one(conn: psycopg.Connection, store: BlobStore, ref: bytes, report: CollectionReport) -> None:
    with conn.transaction():
        locked = conn.execute("SELECT pg_try_advisory_xact_lock(ledger.attachment_lock_namespace(), "
                              "ledger.attachment_lock_key(%s))", (ref,)).fetchone()[0]
        if not locked:
            report.skipped_locked += 1
            return
        if conn.execute("SELECT EXISTS (SELECT 1 FROM ledger.events WHERE attachment_ref = %s)", (ref,)).fetchone()[0]:
            report.kept_referenced += 1
            return
        store.delete(ref)
        report.deleted += 1
