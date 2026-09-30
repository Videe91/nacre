"""
Functionality: Apply the ordered SQL migrations in schema/sql/ to one database, each exactly once.
Owns: migration discovery and naming rules, the applied-migrations record, checksum drift
  detection, one transaction per migration, and a lock against concurrent runs.
Public entry: apply_migrations()
Decisions: D-0003, D-0005, D-0006
Assumptions: A-0002
Notes: Migrations are immutable once applied, like accepted ADRs: an applied file whose checksum
  changed, a missing applied file, or a new file numbered below an applied one is an error, never
  silently re-run. Fix forward with a new file.
  The connection is the admin account from NACRE_DSN_MIGRATOR (it must be able to create roles).
  Each file does SET LOCAL ROLE nacre_migrator so every object is owned by nacre_migrator,
  whoever runs it. RESET ROLE follows before the run is recorded.
  The record table public.nacre_schema_migrations belongs to the admin account, not the app.
"""
import hashlib
import re
from pathlib import Path

import psycopg

SQL_DIR = Path(__file__).parent / "sql"
_FILE_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")
_RUN_LOCK_KEY = 7_236_828_547_001  # arbitrary constant; serializes concurrent migration runs


class MigrationError(Exception):
    """The migration set on disk and the database's applied record disagree, or a file is misnamed."""


def apply_migrations(conn: psycopg.Connection, sql_dir: Path = SQL_DIR) -> list[str]:
    """Apply every pending migration in order. Returns the names applied by this call."""
    files = _discover(sql_dir)
    conn.commit()
    conn.execute("SELECT pg_advisory_lock(%s)", (_RUN_LOCK_KEY,))
    conn.commit()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS public.nacre_schema_migrations (
              version    text        PRIMARY KEY,
              name       text        NOT NULL,
              sha256     bytea       NOT NULL,
              applied_at timestamptz NOT NULL DEFAULT now())""")
        applied = {v: (n, bytes(s)) for v, n, s in conn.execute(
            "SELECT version, name, sha256 FROM public.nacre_schema_migrations")}
        conn.commit()
        _check_applied(files, applied)

        done = []
        for version, path, text, digest in files:
            if version in applied:
                continue
            conn.execute(text)
            conn.execute("RESET ROLE")
            conn.execute(
                "INSERT INTO public.nacre_schema_migrations (version, name, sha256) VALUES (%s, %s, %s)",
                (version, path.name, digest))
            conn.commit()
            done.append(path.name)
        return done
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (_RUN_LOCK_KEY,))
        conn.commit()


def _discover(sql_dir: Path) -> list[tuple[str, Path, str, bytes]]:
    files = []
    for path in sorted(sql_dir.iterdir()):
        if path.suffix != ".sql":
            continue
        match = _FILE_NAME.match(path.name)
        if not match:
            raise MigrationError(f"{path.name}: migration names must look like 0001_short_name.sql")
        raw = path.read_bytes()
        files.append((match.group(1), path, raw.decode("utf-8"), hashlib.sha256(raw).digest()))
    versions = [v for v, *_ in files]
    if len(set(versions)) != len(versions):
        raise MigrationError(f"duplicate migration numbers in {sql_dir}")
    return files


def _check_applied(files: list[tuple[str, Path, str, bytes]], applied: dict[str, tuple[str, bytes]]) -> None:
    on_disk = {v: (p.name, d) for v, p, _, d in files}
    for version, (name, digest) in applied.items():
        if version not in on_disk:
            raise MigrationError(f"applied migration {name} is missing from disk")
        if on_disk[version] != (name, digest):
            raise MigrationError(f"applied migration {name} changed on disk; fix forward with a new file")
    if applied:
        highest = max(applied)
        late = [p.name for v, p, _, _ in files if v not in applied and v < highest]
        if late:
            raise MigrationError(f"new migrations numbered below applied {highest}: {late}")
