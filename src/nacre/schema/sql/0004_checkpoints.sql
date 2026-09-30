-- 0004_checkpoints: the nacre_checkpointer role and the append-only checkpoints table.
-- Decisions: D-0003 (signed checkpoints of stream heads), D-0005 amendment 2 (C-1..C-6).
-- The signing key never enters the database (C-4): rows carry only the public key's id and the
-- signature. Public keys are not stored here either. A verifier must trust only public keys from
-- its own configuration, because anyone able to insert rows could insert a key of their own.

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nacre_checkpointer') THEN
    CREATE ROLE nacre_checkpointer NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
  END IF;
END
$$;

SET LOCAL ROLE nacre_migrator;

CREATE TABLE ledger.checkpoints (
  checkpoint_id  uuid        NOT NULL PRIMARY KEY,
  stream_id      uuid        NOT NULL,
  commit_seq     bigint      NOT NULL CHECK (commit_seq >= 1),
  head_hash      bytea       NOT NULL CHECK (octet_length(head_hash) = 32),
  signed_at      timestamptz NOT NULL,
  signing_key_id text        NOT NULL CHECK (signing_key_id ~ '^[A-Za-z0-9._:-]{1,64}$'),
  signature      bytea       NOT NULL CHECK (octet_length(signature) = 64),  -- Ed25519
  UNIQUE (stream_id, commit_seq)
);

-- C-3: append-only, superuser-proof, same trigger function as ledger.events.
CREATE TRIGGER checkpoints_no_update_delete BEFORE UPDATE OR DELETE ON ledger.checkpoints
  FOR EACH ROW EXECUTE FUNCTION ledger.reject_mutation();
CREATE TRIGGER checkpoints_no_truncate BEFORE TRUNCATE ON ledger.checkpoints
  FOR EACH STATEMENT EXECUTE FUNCTION ledger.reject_mutation();

ALTER TABLE ledger.checkpoints ENABLE ROW LEVEL SECURITY;
ALTER TABLE ledger.checkpoints FORCE ROW LEVEL SECURITY;

-- C-1: the checkpointer reads every stream's heads, but only these three columns.
CREATE POLICY events_checkpointer_read ON ledger.events FOR SELECT TO nacre_checkpointer
  USING (true);
-- C-2: it records and reads checkpoints, nothing more.
CREATE POLICY checkpoints_checkpointer_read ON ledger.checkpoints FOR SELECT TO nacre_checkpointer
  USING (true);
CREATE POLICY checkpoints_checkpointer_record ON ledger.checkpoints FOR INSERT TO nacre_checkpointer
  WITH CHECK (true);
-- C-5: the verifier reads checkpoints; it stays read-only.
CREATE POLICY checkpoints_verifier_read ON ledger.checkpoints FOR SELECT TO nacre_verifier
  USING (true);

REVOKE ALL ON ledger.checkpoints FROM PUBLIC;
GRANT USAGE ON SCHEMA ledger TO nacre_checkpointer;
GRANT SELECT (stream_id, commit_seq, hash) ON ledger.events TO nacre_checkpointer;
GRANT SELECT, INSERT ON ledger.checkpoints TO nacre_checkpointer;
GRANT SELECT ON ledger.checkpoints TO nacre_verifier;
