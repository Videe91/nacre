-- 0010_projection_generations: D-0017 amendment 2. The `interp` projection is organised in generations per stream.
-- Readers use only the stream's ACTIVE generation (the latest recorded switch; 1 when none). A rebuild recomputes the
-- projection from the ledger into a NEW generation and, on a mismatch, writes it and records the switch in one
-- transaction. Old generations are retained (append-only, like everything else).

SET LOCAL ROLE nacre_migrator;

ALTER TABLE interp.edges DROP CONSTRAINT edges_object_id_version_fkey;
ALTER TABLE interp.edges DROP CONSTRAINT edges_pkey;
ALTER TABLE interp.versions DROP CONSTRAINT versions_pkey;
ALTER TABLE interp.versions DROP CONSTRAINT versions_event_id_key;

ALTER TABLE interp.versions ADD COLUMN generation integer NOT NULL DEFAULT 1 CHECK (generation >= 1);
ALTER TABLE interp.edges ADD COLUMN generation integer NOT NULL DEFAULT 1 CHECK (generation >= 1);
ALTER TABLE interp.versions ADD PRIMARY KEY (object_id, version, generation);
ALTER TABLE interp.versions ADD UNIQUE (event_id, generation);
ALTER TABLE interp.edges ADD PRIMARY KEY (object_id, version, generation, ordinal);
ALTER TABLE interp.edges ADD FOREIGN KEY (object_id, version, generation) REFERENCES interp.versions (object_id, version, generation);

CREATE TABLE interp.generation_switches (
  stream_id    uuid         NOT NULL,
  generation   integer      NOT NULL CHECK (generation >= 2),
  switched_at  timestamptz  NOT NULL DEFAULT now(),
  reason       text         NOT NULL,
  differences  integer      NOT NULL CHECK (differences >= 1),
  PRIMARY KEY (stream_id, generation)
);

CREATE FUNCTION interp.active_generation(p_stream uuid) RETURNS integer
  LANGUAGE sql STABLE SET search_path = pg_catalog, pg_temp
  AS $$ SELECT coalesce(max(generation), 1) FROM interp.generation_switches WHERE stream_id = p_stream $$;

-- Re-define the checks per generation (contiguity, kind/stream stability, backing event, earlier-commit edges).
CREATE OR REPLACE FUNCTION interp.check_version() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
DECLARE prev record; ev record;
BEGIN
  SELECT kind, stream_id, version INTO prev FROM interp.versions
   WHERE object_id = NEW.object_id AND generation = NEW.generation ORDER BY version DESC LIMIT 1;
  IF prev IS NULL AND NEW.version <> 1 THEN
    RAISE EXCEPTION 'interp: first version of % must be 1', NEW.object_id;
  ELSIF prev IS NOT NULL AND (NEW.version <> prev.version + 1 OR NEW.kind <> prev.kind OR NEW.stream_id <> prev.stream_id) THEN
    RAISE EXCEPTION 'interp: % version % must follow % with the same kind and stream', NEW.object_id, NEW.version, prev.version;
  END IF;
  SELECT stream_id, commit_seq, event_type INTO ev FROM ledger.events WHERE event_id = NEW.event_id;
  IF ev IS NULL OR ev.stream_id <> NEW.stream_id OR ev.commit_seq <> NEW.commit_seq OR ev.event_type <> 'memory_event' THEN
    RAISE EXCEPTION 'interp: version % of % must be backed by a memory_event of its stream', NEW.version, NEW.object_id;
  END IF;
  IF NEW.generation > interp.active_generation(NEW.stream_id) + 1 THEN
    RAISE EXCEPTION 'interp: generation % skips ahead of the active generation', NEW.generation;
  END IF;
  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION interp.check_edge() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
DECLARE v_stream uuid; v_seq bigint; t_seq bigint;
BEGIN
  SELECT stream_id, commit_seq INTO v_stream, v_seq FROM interp.versions
   WHERE object_id = NEW.object_id AND version = NEW.version AND generation = NEW.generation;
  IF NEW.stream_id <> v_stream THEN
    RAISE EXCEPTION 'interp: edge stream differs from its version''s stream';
  END IF;
  IF NEW.target_event_id IS NOT NULL THEN
    SELECT commit_seq INTO t_seq FROM ledger.events WHERE event_id = NEW.target_event_id AND stream_id = NEW.stream_id;
  ELSE
    SELECT commit_seq INTO t_seq FROM interp.versions
     WHERE object_id = NEW.target_object_id AND version = NEW.target_version AND stream_id = NEW.stream_id
       AND generation = NEW.generation;
  END IF;
  IF t_seq IS NULL OR t_seq >= v_seq THEN
    RAISE EXCEPTION 'interp: edge target must be an earlier commit of the same stream';
  END IF;
  RETURN NEW;
END $$;

-- A switch must point at a generation that exists, and must be the next one. (Mutation 2026-10-01: the "next" rule
-- is an equivalent mutant today, because check_version already forbids rows beyond active+1; kept as defence in depth.)
CREATE FUNCTION interp.check_switch() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF NEW.generation <> interp.active_generation(NEW.stream_id) + 1 THEN
    RAISE EXCEPTION 'interp: a switch must move to the next generation';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM interp.versions WHERE stream_id = NEW.stream_id AND generation = NEW.generation) THEN
    RAISE EXCEPTION 'interp: cannot switch to an empty generation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER switches_check BEFORE INSERT ON interp.generation_switches FOR EACH ROW EXECUTE FUNCTION interp.check_switch();
CREATE TRIGGER switches_no_update_delete BEFORE UPDATE OR DELETE ON interp.generation_switches
  FOR EACH ROW EXECUTE FUNCTION ledger.reject_mutation();
CREATE TRIGGER switches_no_truncate BEFORE TRUNCATE ON interp.generation_switches
  FOR EACH STATEMENT EXECUTE FUNCTION ledger.reject_mutation();

CREATE OR REPLACE VIEW interp.heads WITH (security_invoker = true) AS
  SELECT DISTINCT ON (object_id) * FROM interp.versions
   WHERE generation = interp.active_generation(stream_id) ORDER BY object_id, version DESC;

ALTER TABLE interp.generation_switches ENABLE ROW LEVEL SECURITY;
ALTER TABLE interp.generation_switches FORCE ROW LEVEL SECURITY;
CREATE POLICY switches_app_read ON interp.generation_switches FOR SELECT TO nacre_app
  USING (stream_id = ANY (scopes.read_streams()));
CREATE POLICY switches_app_write ON interp.generation_switches FOR INSERT TO nacre_app
  WITH CHECK (stream_id = ANY (scopes.write_streams()));
REVOKE ALL ON interp.generation_switches FROM PUBLIC;
GRANT SELECT, INSERT ON interp.generation_switches TO nacre_app;
REVOKE ALL ON FUNCTION interp.active_generation(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION interp.active_generation(uuid) TO nacre_app;
GRANT USAGE ON SCHEMA interp TO nacre_app;
