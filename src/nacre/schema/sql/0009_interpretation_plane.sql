-- 0009_interpretation_plane: the `interp` projection of interpretation versions (D-0017, amendment 1).
-- The LEDGER is the truth: every version is a memory_event (op `version`) whose encrypted body holds the content.
-- These tables hold only structural fields, never content plaintext (D-0017, D3): ids, version, kind, status,
-- support level, pinned edges, span offsets and keyed MACs. They are append-only, scoped by RLS exactly like the
-- ledger, written by nacre_app in the same transaction as the event, and rebuildable from the ledger.

SET LOCAL ROLE nacre_migrator;

CREATE SCHEMA interp;
REVOKE ALL ON SCHEMA interp FROM PUBLIC;

CREATE TABLE interp.versions (
  object_id    uuid        NOT NULL,
  version      integer     NOT NULL CHECK (version >= 1),
  kind         text        NOT NULL CHECK (kind IN ('belief', 'fallback', 'episode')),
  status       text        NOT NULL CHECK (status IN ('active', 'contested', 'superseded', 'fallback')),
  support      text        CHECK (support IN ('single_source', 'quorum')),
  stream_id    uuid        NOT NULL,
  event_id     uuid        NOT NULL UNIQUE,   -- backing checked by trigger, not a FOREIGN KEY: an FK would change how
                                             -- the ledger refuses TRUNCATE (FK error before its append-only trigger)
  commit_seq   bigint      NOT NULL,
  content_mac  bytea       NOT NULL CHECK (octet_length(content_mac) = 32),
  PRIMARY KEY (object_id, version),
  CHECK ((kind = 'belief') = (support IS NOT NULL)),
  CHECK ((kind = 'fallback') = (status = 'fallback'))
);

CREATE TABLE interp.edges (
  object_id         uuid     NOT NULL,
  version           integer  NOT NULL,
  ordinal           integer  NOT NULL CHECK (ordinal >= 0),
  stream_id         uuid     NOT NULL,
  role              text     NOT NULL CHECK (role IN ('support', 'contradiction', 'superseded_by', 'derived_from', 'member')),
  target_event_id   uuid,
  target_object_id  uuid,
  target_version    integer,
  span_start        integer,
  span_end          integer,
  span_mac          bytea    CHECK (span_mac IS NULL OR octet_length(span_mac) = 32),
  PRIMARY KEY (object_id, version, ordinal),
  FOREIGN KEY (object_id, version) REFERENCES interp.versions (object_id, version),
  CHECK ((target_event_id IS NULL) <> (target_object_id IS NULL)),
  CHECK ((target_object_id IS NULL) = (target_version IS NULL)),
  CHECK ((span_start IS NULL) = (span_end IS NULL) AND (span_start IS NULL) = (span_mac IS NULL)),
  CHECK (span_start IS NULL OR (span_start >= 0 AND span_end > span_start)),
  CHECK (span_start IS NULL OR target_event_id IS NOT NULL)
);
CREATE INDEX edges_target_event ON interp.edges (target_event_id);
CREATE INDEX edges_target_object ON interp.edges (target_object_id, target_version);

-- Versions are contiguous per object; kind and stream never change; the backing event is a memory_event of the same
-- stream at the recorded commit_seq.
CREATE FUNCTION interp.check_version() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
DECLARE prev record; ev record;
BEGIN
  SELECT kind, stream_id, version INTO prev FROM interp.versions
   WHERE object_id = NEW.object_id ORDER BY version DESC LIMIT 1;
  IF prev IS NULL AND NEW.version <> 1 THEN
    RAISE EXCEPTION 'interp: first version of % must be 1', NEW.object_id;
  ELSIF prev IS NOT NULL AND (NEW.version <> prev.version + 1 OR NEW.kind <> prev.kind OR NEW.stream_id <> prev.stream_id) THEN
    RAISE EXCEPTION 'interp: % version % must follow % with the same kind and stream', NEW.object_id, NEW.version, prev.version;
  END IF;
  SELECT stream_id, commit_seq, event_type INTO ev FROM ledger.events WHERE event_id = NEW.event_id;
  IF ev IS NULL OR ev.stream_id <> NEW.stream_id OR ev.commit_seq <> NEW.commit_seq OR ev.event_type <> 'memory_event' THEN
    RAISE EXCEPTION 'interp: version % of % must be backed by a memory_event of its stream', NEW.version, NEW.object_id;
  END IF;
  RETURN NEW;
END $$;

CREATE FUNCTION interp.check_edge() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
DECLARE v_stream uuid; v_seq bigint; t_seq bigint;
BEGIN
  SELECT stream_id, commit_seq INTO v_stream, v_seq FROM interp.versions WHERE object_id = NEW.object_id AND version = NEW.version;
  IF NEW.stream_id <> v_stream THEN
    RAISE EXCEPTION 'interp: edge stream differs from its version''s stream';
  END IF;
  -- Strict pre-existence in commit order (MNEXA ADR-0005 L-7): targets were committed before the version.
  IF NEW.target_event_id IS NOT NULL THEN
    SELECT commit_seq INTO t_seq FROM ledger.events WHERE event_id = NEW.target_event_id AND stream_id = NEW.stream_id;
  ELSE
    SELECT commit_seq INTO t_seq FROM interp.versions
     WHERE object_id = NEW.target_object_id AND version = NEW.target_version AND stream_id = NEW.stream_id;
  END IF;
  IF t_seq IS NULL OR t_seq >= v_seq THEN
    RAISE EXCEPTION 'interp: edge target must be an earlier commit of the same stream';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER versions_check BEFORE INSERT ON interp.versions FOR EACH ROW EXECUTE FUNCTION interp.check_version();
CREATE TRIGGER edges_check BEFORE INSERT ON interp.edges FOR EACH ROW EXECUTE FUNCTION interp.check_edge();
CREATE TRIGGER versions_no_update_delete BEFORE UPDATE OR DELETE ON interp.versions
  FOR EACH ROW EXECUTE FUNCTION ledger.reject_mutation();
CREATE TRIGGER versions_no_truncate BEFORE TRUNCATE ON interp.versions
  FOR EACH STATEMENT EXECUTE FUNCTION ledger.reject_mutation();
CREATE TRIGGER edges_no_update_delete BEFORE UPDATE OR DELETE ON interp.edges
  FOR EACH ROW EXECUTE FUNCTION ledger.reject_mutation();
CREATE TRIGGER edges_no_truncate BEFORE TRUNCATE ON interp.edges
  FOR EACH STATEMENT EXECUTE FUNCTION ledger.reject_mutation();

CREATE VIEW interp.heads WITH (security_invoker = true) AS
  SELECT DISTINCT ON (object_id) * FROM interp.versions ORDER BY object_id, version DESC;

ALTER TABLE interp.versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE interp.versions FORCE ROW LEVEL SECURITY;
ALTER TABLE interp.edges ENABLE ROW LEVEL SECURITY;
ALTER TABLE interp.edges FORCE ROW LEVEL SECURITY;
CREATE POLICY versions_app_read ON interp.versions FOR SELECT TO nacre_app USING (stream_id = ANY (scopes.read_streams()));
CREATE POLICY versions_app_write ON interp.versions FOR INSERT TO nacre_app WITH CHECK (stream_id = ANY (scopes.write_streams()));
CREATE POLICY edges_app_read ON interp.edges FOR SELECT TO nacre_app USING (stream_id = ANY (scopes.read_streams()));
CREATE POLICY edges_app_write ON interp.edges FOR INSERT TO nacre_app WITH CHECK (stream_id = ANY (scopes.write_streams()));

GRANT USAGE ON SCHEMA interp TO nacre_app;
GRANT SELECT, INSERT ON interp.versions, interp.edges TO nacre_app;
GRANT SELECT ON interp.heads TO nacre_app;
