-- 0003_keys: wrapped key storage for crypto-shredding, behind its own row-level security.
-- Decisions: D-0004 (root KEK -> stream master key -> data key per (stream, subject, month)),
--            D-0005 (keys behind the same scope wall; the verifier has no access).
-- Destroying a key = deleting its row. Delete and rotation policies arrive with the files that
-- need them (keys/shred_keys.py, keys/rotate_root_key.py) in later migrations.

SET LOCAL ROLE nacre_migrator;

CREATE SCHEMA keys;
REVOKE ALL ON SCHEMA keys FROM PUBLIC;

-- One master key per stream, wrapped by the root key (via core/root_key_provider.py).
CREATE TABLE keys.stream_master_keys (
  stream_id        uuid        NOT NULL PRIMARY KEY,
  root_key_version text        NOT NULL CHECK (root_key_version ~ '^[A-Za-z0-9._:-]{1,64}$'),
  wrapped_key      bytea       NOT NULL,
  created_at       timestamptz NOT NULL DEFAULT now()
);

-- Data keys, wrapped by their stream's master key. subject_id is a person's opaque id, or the
-- stream_id itself for the stream's system subject. month is the first day of a UTC calendar month.
-- Deleting a stream master key (scope deletion) cascades to every data key of the stream.
CREATE TABLE keys.data_keys (
  key_id           uuid        NOT NULL PRIMARY KEY,
  stream_id        uuid        NOT NULL REFERENCES keys.stream_master_keys (stream_id) ON DELETE CASCADE,
  subject_id       uuid        NOT NULL,
  month            date        NOT NULL CHECK (month = date_trunc('month', month)::date),
  wrapped_key      bytea       NOT NULL,
  -- A-0015: hard cap of 2^28 encryptions per data key.
  encryption_count bigint      NOT NULL DEFAULT 0 CHECK (encryption_count BETWEEN 0 AND 268435456),
  created_at       timestamptz NOT NULL DEFAULT now(),
  UNIQUE (stream_id, subject_id, month)
);

ALTER TABLE keys.stream_master_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE keys.stream_master_keys FORCE ROW LEVEL SECURITY;
ALTER TABLE keys.data_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE keys.data_keys FORCE ROW LEVEL SECURITY;

CREATE POLICY master_keys_app_read ON keys.stream_master_keys FOR SELECT TO nacre_app
  USING (stream_id = ANY (scopes.read_streams()));
CREATE POLICY master_keys_app_create ON keys.stream_master_keys FOR INSERT TO nacre_app
  WITH CHECK (stream_id = ANY (scopes.write_streams()));

CREATE POLICY data_keys_app_read ON keys.data_keys FOR SELECT TO nacre_app
  USING (stream_id = ANY (scopes.read_streams()));
CREATE POLICY data_keys_app_create ON keys.data_keys FOR INSERT TO nacre_app
  WITH CHECK (stream_id = ANY (scopes.write_streams()));
CREATE POLICY data_keys_app_count ON keys.data_keys FOR UPDATE TO nacre_app
  USING (stream_id = ANY (scopes.write_streams()))
  WITH CHECK (stream_id = ANY (scopes.write_streams()));

REVOKE ALL ON keys.stream_master_keys, keys.data_keys FROM PUBLIC;
GRANT USAGE ON SCHEMA keys TO nacre_app;
GRANT SELECT, INSERT ON keys.stream_master_keys, keys.data_keys TO nacre_app;
GRANT UPDATE (encryption_count) ON keys.data_keys TO nacre_app;
