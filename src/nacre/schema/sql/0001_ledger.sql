-- 0001_ledger: roles, the events table, append-only and seal-linkage enforcement.
-- Decisions: D-0002 (envelope columns), D-0003 (append-only, linkage, stream lock), D-0005 (roles).
-- Migrations are immutable once applied (checksummed by schema/apply_migrations.py).

-- Roles are cluster-wide and created NOLOGIN. Login and passwords are set by ops or tests, never here.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nacre_migrator') THEN
    CREATE ROLE nacre_migrator NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nacre_app') THEN
    CREATE ROLE nacre_app NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nacre_verifier') THEN
    CREATE ROLE nacre_verifier NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
  END IF;
  EXECUTE format('GRANT CREATE ON DATABASE %I TO nacre_migrator', current_database());
END
$$;

-- Everything below is owned by nacre_migrator, never by the app or the verifier (D-0005 S-1).
SET LOCAL ROLE nacre_migrator;

CREATE SCHEMA ledger;
REVOKE ALL ON SCHEMA ledger FROM PUBLIC;

CREATE TABLE ledger.events (
  envelope_version      smallint    NOT NULL CHECK (envelope_version = 1),
  event_id              uuid        NOT NULL PRIMARY KEY,
  stream_id             uuid        NOT NULL,
  org_id                uuid,
  project_id            uuid,
  user_id               uuid,
  agent_id              uuid,
  task_id               uuid,
  commit_seq            bigint      NOT NULL CHECK (commit_seq >= 1),
  occurred_at           timestamptz,
  occurred_at_basis     text        CHECK (occurred_at_basis IN ('observed', 'asserted')),
  occurred_at_precision text        CHECK (occurred_at_precision IN
                          ('year', 'month', 'day', 'hour', 'minute', 'second', 'millisecond', 'microsecond')),
  recorded_at           timestamptz NOT NULL,
  committed_at          timestamptz NOT NULL,
  event_type            text        NOT NULL CHECK (event_type IN
                          ('message', 'action', 'result', 'prediction', 'decision', 'outcome', 'statement',
                           'memory_event', 'config_event', 'correction', 'deletion_marker')),
  payload_type          text        NOT NULL CHECK (payload_type IN
                          ('text', 'image', 'audio', 'diff', 'table', 'structured', 'trace')),
  actor_kind            text        NOT NULL CHECK (actor_kind IN ('person', 'agent', 'model', 'tool', 'system')),
  actor_id              uuid        NOT NULL,
  -- Short identifiers (D-0002): restricted charset, no '@' or spaces, so no identity or content hides here.
  actor_model           text        CHECK (actor_model ~ '^[A-Za-z0-9._:/+-]{1,128}$'),
  actor_model_version   text        CHECK (actor_model_version ~ '^[A-Za-z0-9._:/+-]{1,128}$'),
  actor_tool            text        CHECK (actor_tool ~ '^[A-Za-z0-9._:/+-]{1,128}$'),
  source                text        NOT NULL CHECK (source IN ('chat', 'git', 'ci', 'review', 'web', 'tool', 'system')),
  trust                 text        NOT NULL CHECK (trust IN ('trusted', 'untrusted')),
  caused_by             uuid,
  cycle_id              uuid,
  config_version        text        CHECK (config_version ~ '^[A-Za-z0-9._:/+-]{1,128}$'),
  mode                  text        CHECK (mode IN ('normal', 'incident', 'onboarding', 'exploration')),
  key_id                uuid        NOT NULL,
  idempotency_key       text        NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 200),
  request_mac           bytea       NOT NULL CHECK (octet_length(request_mac) = 32),
  attachment_ref        bytea       CHECK (octet_length(attachment_ref) = 32),
  attachment_sha256     bytea       CHECK (octet_length(attachment_sha256) = 32),
  body_ciphertext       bytea       NOT NULL,
  prev_hash             bytea       NOT NULL CHECK (octet_length(prev_hash) = 32),
  hash                  bytea       NOT NULL CHECK (octet_length(hash) = 32),
  -- MNEXA ADR-0010 rules 2 and 4: basis and precision exist exactly when occurred_at does.
  CHECK ((occurred_at IS NULL) = (occurred_at_basis IS NULL)),
  CHECK ((occurred_at IS NULL) = (occurred_at_precision IS NULL)),
  -- D-0002: 'observed' world time only from a trusted runtime.
  CHECK (occurred_at_basis IS DISTINCT FROM 'observed' OR trust = 'trusted'),
  CHECK (event_type <> 'correction' OR caused_by IS NOT NULL),
  CHECK ((attachment_ref IS NULL) = (attachment_sha256 IS NULL)),
  UNIQUE (stream_id, commit_seq),
  UNIQUE (stream_id, idempotency_key)
);

CREATE INDEX events_stream_cycle ON ledger.events (stream_id, cycle_id) WHERE cycle_id IS NOT NULL;
CREATE INDEX events_type_time ON ledger.events (event_type, recorded_at);

-- The one definition of a stream's lock key; ledger/append_event.py calls this too (D-0003).
CREATE FUNCTION ledger.stream_lock_key(p_stream_id uuid) RETURNS bigint
  LANGUAGE sql IMMUTABLE PARALLEL SAFE
  SET search_path = pg_catalog, pg_temp
  AS $$ SELECT hashtextextended(p_stream_id::text, 0) $$;

-- Append-only for every role, the owner included (D-0003). Only an explicit
-- ALTER TABLE ... DISABLE TRIGGER by the owner in a migration can get past it.
CREATE FUNCTION ledger.reject_mutation() RETURNS trigger
  LANGUAGE plpgsql
  SET search_path = pg_catalog, pg_temp
  AS $$
BEGIN
  RAISE EXCEPTION '% on %.% is forbidden: the table is append-only (D-0003)', TG_OP, TG_TABLE_SCHEMA, TG_TABLE_NAME
    USING ERRCODE = 'insufficient_privilege';
END
$$;

CREATE TRIGGER events_no_update_delete BEFORE UPDATE OR DELETE ON ledger.events
  FOR EACH ROW EXECUTE FUNCTION ledger.reject_mutation();
CREATE TRIGGER events_no_truncate BEFORE TRUNCATE ON ledger.events
  FOR EACH STATEMENT EXECUTE FUNCTION ledger.reject_mutation();

-- Seal linkage (D-0003): gapless commit_seq and prev_hash = previous hash, checked by the DB.
-- The trigger takes the stream lock itself, so linkage holds even for a writer that forgot to.
-- Under READ COMMITTED each statement below takes a fresh snapshot after the lock is held,
-- so the head it reads is the latest committed one (visibility order, MNEXA ADR-0010 rule 9).
-- It runs as the invoker: under RLS the head is visible only if the stream is readable,
-- so an append-without-read session fails closed.
CREATE FUNCTION ledger.check_linkage() RETURNS trigger
  LANGUAGE plpgsql
  SET search_path = pg_catalog, pg_temp
  AS $$
DECLARE
  head_seq  bigint;
  head_hash bytea;
BEGIN
  PERFORM pg_advisory_xact_lock(ledger.stream_lock_key(NEW.stream_id));
  SELECT e.commit_seq, e.hash INTO head_seq, head_hash
    FROM ledger.events e
   WHERE e.stream_id = NEW.stream_id
   ORDER BY e.commit_seq DESC
   LIMIT 1;
  IF head_seq IS NULL THEN
    IF NEW.commit_seq <> 1 OR NEW.prev_hash <> decode(repeat('00', 32), 'hex') THEN
      RAISE EXCEPTION 'seal linkage: first event of stream % needs commit_seq 1 and a zero prev_hash', NEW.stream_id
        USING ERRCODE = 'check_violation';
    END IF;
  ELSIF NEW.commit_seq <> head_seq + 1 OR NEW.prev_hash <> head_hash THEN
    RAISE EXCEPTION 'seal linkage: stream % expects commit_seq % chained to the hash of %', NEW.stream_id, head_seq + 1, head_seq
      USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.caused_by IS NOT NULL AND NOT EXISTS (
       SELECT 1 FROM ledger.events c WHERE c.event_id = NEW.caused_by AND c.stream_id = NEW.stream_id) THEN
    RAISE EXCEPTION 'caused_by % is not an earlier event of stream %', NEW.caused_by, NEW.stream_id
      USING ERRCODE = 'foreign_key_violation';
  END IF;
  RETURN NEW;
END
$$;

CREATE TRIGGER events_check_linkage BEFORE INSERT ON ledger.events
  FOR EACH ROW EXECUTE FUNCTION ledger.check_linkage();

-- RLS on and forced from the start with no policies: deny-all until 0002 adds scope policies.
ALTER TABLE ledger.events ENABLE ROW LEVEL SECURITY;
ALTER TABLE ledger.events FORCE ROW LEVEL SECURITY;

REVOKE ALL ON ledger.events FROM PUBLIC;
GRANT USAGE ON SCHEMA ledger TO nacre_app, nacre_verifier;
GRANT SELECT, INSERT ON ledger.events TO nacre_app;
GRANT SELECT ON ledger.events TO nacre_verifier;
