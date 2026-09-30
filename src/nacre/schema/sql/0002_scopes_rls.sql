-- 0002_scopes_rls: scope registry, access-grant projection, and row-level security for the ledger.
-- Decisions: D-0005 (RLS on transaction-local settings, one owning stream, grants as projections),
--            D-0002 amendment 3 (no identity columns: principals are opaque ids).
-- Settings (set only via SET LOCAL by scopes/open_scoped_session.py):
--   nacre.principal      opaque principal id; lets a principal read its own grants
--   nacre.read_streams   uuid[] of readable streams
--   nacre.write_streams  uuid[] of appendable streams (always a subset of read_streams)
-- A missing or reset setting reads as the empty set, which yields zero rows (fail closed).

SET LOCAL ROLE nacre_migrator;

CREATE SCHEMA scopes;
REVOKE ALL ON SCHEMA scopes FROM PUBLIC;

CREATE FUNCTION scopes.setting_uuids(p_name text) RETURNS uuid[]
  LANGUAGE sql STABLE
  SET search_path = pg_catalog, pg_temp
  AS $$ SELECT coalesce(nullif(current_setting(p_name, true), '')::uuid[], '{}'::uuid[]) $$;

CREATE FUNCTION scopes.read_streams() RETURNS uuid[]
  LANGUAGE sql STABLE
  SET search_path = pg_catalog, pg_temp
  AS $$ SELECT scopes.setting_uuids('nacre.read_streams') $$;

CREATE FUNCTION scopes.write_streams() RETURNS uuid[]
  LANGUAGE sql STABLE
  SET search_path = pg_catalog, pg_temp
  AS $$ SELECT scopes.setting_uuids('nacre.write_streams') $$;

CREATE FUNCTION scopes.current_principal() RETURNS uuid
  LANGUAGE sql STABLE
  SET search_path = pg_catalog, pg_temp
  AS $$ SELECT nullif(current_setting('nacre.principal', true), '')::uuid $$;

-- One row per durable scope = one stream (D-0005). Tasks are not streams.
CREATE TABLE scopes.scopes (
  stream_id        uuid NOT NULL PRIMARY KEY,
  kind             text NOT NULL CHECK (kind IN ('org', 'team', 'project', 'user', 'agent')),
  org_id           uuid NOT NULL,
  parent_stream_id uuid REFERENCES scopes.scopes (stream_id),
  status           text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'deleted')),
  source_event_id  uuid NOT NULL,
  CHECK ((kind = 'org') = (stream_id = org_id)),
  CHECK (kind <> 'org' OR parent_stream_id IS NULL)
);

-- Derived projection of grant/revoke config events in the org stream (D-0005). Insert-only:
-- each row is a principal's full access to one stream as of org-stream commit source_seq;
-- the row with the highest source_seq wins. A revoke is a row with both flags false.
CREATE TABLE scopes.scope_grants (
  principal_id    uuid    NOT NULL,
  stream_id       uuid    NOT NULL REFERENCES scopes.scopes (stream_id),
  org_id          uuid    NOT NULL,
  can_read        boolean NOT NULL,
  can_append      boolean NOT NULL,
  source_event_id uuid    NOT NULL,
  source_seq      bigint  NOT NULL CHECK (source_seq >= 1),
  PRIMARY KEY (principal_id, stream_id, source_seq),
  CHECK (NOT can_append OR can_read)
);

CREATE TRIGGER scope_grants_no_update_delete BEFORE UPDATE OR DELETE ON scopes.scope_grants
  FOR EACH ROW EXECUTE FUNCTION ledger.reject_mutation();
CREATE TRIGGER scope_grants_no_truncate BEFORE TRUNCATE ON scopes.scope_grants
  FOR EACH STATEMENT EXECUTE FUNCTION ledger.reject_mutation();

ALTER TABLE scopes.scopes ENABLE ROW LEVEL SECURITY;
ALTER TABLE scopes.scopes FORCE ROW LEVEL SECURITY;
ALTER TABLE scopes.scope_grants ENABLE ROW LEVEL SECURITY;
ALTER TABLE scopes.scope_grants FORCE ROW LEVEL SECURITY;

-- Ledger policies (D-0005).
CREATE POLICY events_app_read ON ledger.events FOR SELECT TO nacre_app
  USING (stream_id = ANY (scopes.read_streams()));
CREATE POLICY events_app_append ON ledger.events FOR INSERT TO nacre_app
  WITH CHECK (stream_id = ANY (scopes.write_streams()));
-- The verifier checks every chain without keys (D-0003); it gets no access to scopes or keys.
CREATE POLICY events_verifier_read ON ledger.events FOR SELECT TO nacre_verifier
  USING (true);

-- Scope registry: visible where readable; registering needs append on the org stream,
-- where the registering config event goes.
CREATE POLICY scopes_app_read ON scopes.scopes FOR SELECT TO nacre_app
  USING (stream_id = ANY (scopes.read_streams()));
CREATE POLICY scopes_app_register ON scopes.scopes FOR INSERT TO nacre_app
  WITH CHECK (org_id = ANY (scopes.write_streams()));

-- Grants: a principal sees its own grants (needed to resolve access before any stream is
-- readable); readers of the org stream see all of that org's grants.
CREATE POLICY grants_app_read ON scopes.scope_grants FOR SELECT TO nacre_app
  USING (principal_id = scopes.current_principal() OR org_id = ANY (scopes.read_streams()));
CREATE POLICY grants_app_record ON scopes.scope_grants FOR INSERT TO nacre_app
  WITH CHECK (org_id = ANY (scopes.write_streams()));

REVOKE ALL ON scopes.scopes, scopes.scope_grants FROM PUBLIC;
GRANT USAGE ON SCHEMA scopes TO nacre_app;
GRANT SELECT, INSERT ON scopes.scopes, scopes.scope_grants TO nacre_app;
