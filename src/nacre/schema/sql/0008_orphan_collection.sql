-- 0008_orphan_collection: the orphan-blob collector's role and the per-attachment lock key (D-0015).
-- nacre_gc reads ONLY ledger.events.attachment_ref (column grant, all streams). No bodies, keys, scopes or
-- checkpoints. ledger.attachment_lock_key() gives the (namespace, key) pair for the two-integer advisory-lock form,
-- which is a separate key space from the one-bigint form used by ledger.stream_lock_key(), so attachment locks and
-- stream locks never collide. Two refs sharing a key only cause a wait or a skipped collection, never a deletion.

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nacre_gc') THEN
    CREATE ROLE nacre_gc NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT;
  END IF;
END
$$;

SET LOCAL ROLE nacre_migrator;

CREATE FUNCTION ledger.attachment_lock_namespace() RETURNS integer
  LANGUAGE sql IMMUTABLE PARALLEL SAFE
  SET search_path = pg_catalog, pg_temp
  AS $$ SELECT 1312899907 $$;                               -- 0x4E414343 'NACC'

CREATE FUNCTION ledger.attachment_lock_key(p_ref bytea) RETURNS integer
  LANGUAGE sql IMMUTABLE PARALLEL SAFE
  SET search_path = pg_catalog, pg_temp
  AS $$ SELECT hashtext(encode(p_ref, 'hex')) $$;

REVOKE ALL ON FUNCTION ledger.attachment_lock_namespace(), ledger.attachment_lock_key(bytea) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ledger.attachment_lock_namespace(), ledger.attachment_lock_key(bytea) TO nacre_app, nacre_gc;

GRANT USAGE ON SCHEMA ledger TO nacre_gc;
GRANT SELECT (attachment_ref) ON ledger.events TO nacre_gc;
CREATE POLICY events_gc_refs ON ledger.events FOR SELECT TO nacre_gc USING (true);
