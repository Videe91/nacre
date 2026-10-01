-- 0012_recall_index: D-0024 (accepted 2026-10-01). The encrypted recall index and the per-stream shred epoch.
-- recall.index_entries holds ONLY ciphertext plus structural ids: no plaintext, no MACs, no norms, no tokens
-- (D-0024 §2). Each body is sealed under a purpose subkey of the indexed version's own (contributor-set) data key,
-- so destroying that key makes the entry unreadable; WAL and backups only ever hold ciphertext.
-- Generations: every generation has exactly one embedder (a model change = a new generation, full re-index); the
-- active generation is the latest recorded switch (1 when none), as for interp (0010). Append-only throughout.
-- keys.shred_epochs: bumped by keyadmin in the same transaction as any key destruction, for every affected stream;
-- a recall reads it inside its snapshot and evicts cached entries whose keys are gone (D-0024 §3).

SET LOCAL ROLE nacre_migrator;

CREATE SCHEMA recall;
REVOKE ALL ON SCHEMA recall FROM PUBLIC;

CREATE TABLE recall.index_generations (
  stream_id    uuid         NOT NULL,
  generation   integer      NOT NULL CHECK (generation >= 1),
  embedder_id  text         NOT NULL CHECK (embedder_id ~ '^[A-Za-z0-9._/@#:+-]{1,200}$'),
  created_at   timestamptz  NOT NULL DEFAULT now(),
  PRIMARY KEY (stream_id, generation)
);

CREATE TABLE recall.index_switches (
  stream_id    uuid         NOT NULL,
  generation   integer      NOT NULL CHECK (generation >= 2),
  switched_at  timestamptz  NOT NULL DEFAULT now(),
  reason       text         NOT NULL CHECK (reason IN ('embedder_change', 'rebuild')),
  PRIMARY KEY (stream_id, generation),
  FOREIGN KEY (stream_id, generation) REFERENCES recall.index_generations (stream_id, generation)
);

CREATE TABLE recall.index_entries (
  stream_id         uuid     NOT NULL,
  index_generation  integer  NOT NULL,
  version_event_id  uuid     NOT NULL,
  key_id            uuid     NOT NULL,
  embedder_id       text     NOT NULL,
  seq               bigint   GENERATED ALWAYS AS IDENTITY UNIQUE,
  body              bytea    NOT NULL,
  PRIMARY KEY (stream_id, index_generation, version_event_id),
  FOREIGN KEY (stream_id, index_generation) REFERENCES recall.index_generations (stream_id, generation)
);
ALTER TABLE recall.index_entries ALTER COLUMN body SET STORAGE EXTERNAL;   -- ciphertext: compression is useless

CREATE FUNCTION recall.active_generation(p_stream uuid) RETURNS integer
  LANGUAGE sql STABLE SET search_path = pg_catalog, pg_temp
  AS $$ SELECT coalesce(max(generation), 1) FROM recall.index_switches WHERE stream_id = p_stream $$;

-- An entry carries its generation's embedder and is backed by a memory_event of its own stream, sealed under the
-- key that event was sealed under.
CREATE FUNCTION recall.check_entry() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
DECLARE g_embedder text; ev record;
BEGIN
  SELECT embedder_id INTO g_embedder FROM recall.index_generations
   WHERE stream_id = NEW.stream_id AND generation = NEW.index_generation;
  IF g_embedder IS DISTINCT FROM NEW.embedder_id THEN
    RAISE EXCEPTION 'recall: entry embedder % differs from generation %''s embedder %', NEW.embedder_id,
      NEW.index_generation, g_embedder;
  END IF;
  SELECT stream_id, event_type, key_id INTO ev FROM ledger.events WHERE event_id = NEW.version_event_id;
  IF ev IS NULL OR ev.stream_id <> NEW.stream_id OR ev.event_type <> 'memory_event' OR ev.key_id <> NEW.key_id THEN
    RAISE EXCEPTION 'recall: entry for % must be backed by a memory_event of its stream under the same key',
      NEW.version_event_id;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER index_entries_check BEFORE INSERT ON recall.index_entries
  FOR EACH ROW EXECUTE FUNCTION recall.check_entry();

-- A switch may only move forward by one, to a generation that exists.
CREATE FUNCTION recall.check_switch() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF NEW.generation <> recall.active_generation(NEW.stream_id) + 1 THEN
    RAISE EXCEPTION 'recall: switch to % must follow the active generation %', NEW.generation,
      recall.active_generation(NEW.stream_id);
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER index_switches_check BEFORE INSERT ON recall.index_switches
  FOR EACH ROW EXECUTE FUNCTION recall.check_switch();

CREATE TRIGGER index_generations_no_update_delete BEFORE UPDATE OR DELETE ON recall.index_generations
  FOR EACH ROW EXECUTE FUNCTION ledger.reject_mutation();
CREATE TRIGGER index_switches_no_update_delete BEFORE UPDATE OR DELETE ON recall.index_switches
  FOR EACH ROW EXECUTE FUNCTION ledger.reject_mutation();
CREATE TRIGGER index_entries_no_update_delete BEFORE UPDATE OR DELETE ON recall.index_entries
  FOR EACH ROW EXECUTE FUNCTION ledger.reject_mutation();
CREATE TRIGGER index_generations_no_truncate BEFORE TRUNCATE ON recall.index_generations
  FOR EACH STATEMENT EXECUTE FUNCTION ledger.reject_mutation();
CREATE TRIGGER index_switches_no_truncate BEFORE TRUNCATE ON recall.index_switches
  FOR EACH STATEMENT EXECUTE FUNCTION ledger.reject_mutation();
CREATE TRIGGER index_entries_no_truncate BEFORE TRUNCATE ON recall.index_entries
  FOR EACH STATEMENT EXECUTE FUNCTION ledger.reject_mutation();

ALTER TABLE recall.index_generations ENABLE ROW LEVEL SECURITY;
ALTER TABLE recall.index_generations FORCE ROW LEVEL SECURITY;
ALTER TABLE recall.index_switches ENABLE ROW LEVEL SECURITY;
ALTER TABLE recall.index_switches FORCE ROW LEVEL SECURITY;
ALTER TABLE recall.index_entries ENABLE ROW LEVEL SECURITY;
ALTER TABLE recall.index_entries FORCE ROW LEVEL SECURITY;
CREATE POLICY generations_app_read ON recall.index_generations FOR SELECT TO nacre_app USING (stream_id = ANY (scopes.read_streams()));
CREATE POLICY generations_app_write ON recall.index_generations FOR INSERT TO nacre_app WITH CHECK (stream_id = ANY (scopes.write_streams()));
CREATE POLICY switches_app_read ON recall.index_switches FOR SELECT TO nacre_app USING (stream_id = ANY (scopes.read_streams()));
CREATE POLICY switches_app_write ON recall.index_switches FOR INSERT TO nacre_app WITH CHECK (stream_id = ANY (scopes.write_streams()));
CREATE POLICY entries_app_read ON recall.index_entries FOR SELECT TO nacre_app USING (stream_id = ANY (scopes.read_streams()));
CREATE POLICY entries_app_write ON recall.index_entries FOR INSERT TO nacre_app WITH CHECK (stream_id = ANY (scopes.write_streams()));

GRANT USAGE ON SCHEMA recall TO nacre_app;
GRANT SELECT, INSERT ON recall.index_generations, recall.index_switches, recall.index_entries TO nacre_app;

-- Per-stream shred epoch (D-0024 §3; per stream rather than per org so it stays inside per-stream RLS).
CREATE TABLE keys.shred_epochs (
  stream_id  uuid    NOT NULL PRIMARY KEY,
  epoch      bigint  NOT NULL CHECK (epoch >= 1)
);
ALTER TABLE keys.shred_epochs ENABLE ROW LEVEL SECURITY;
ALTER TABLE keys.shred_epochs FORCE ROW LEVEL SECURITY;
CREATE POLICY shred_epochs_app_read ON keys.shred_epochs FOR SELECT TO nacre_app USING (stream_id = ANY (scopes.read_streams()));
CREATE POLICY shred_epochs_keyadmin ON keys.shred_epochs FOR ALL TO nacre_keyadmin USING (true) WITH CHECK (true);
REVOKE ALL ON keys.shred_epochs FROM PUBLIC;
GRANT SELECT ON keys.shred_epochs TO nacre_app;
GRANT SELECT, INSERT, UPDATE ON keys.shred_epochs TO nacre_keyadmin;
