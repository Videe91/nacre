-- 0013_auth: principals, tokens, delegations and reviewer grants (D-0026 + amendments 1-2, owner 2026-10-02).
-- Roles (amendment 2, owner conditions):
--   nacre_auth             token lookup only: SELECT tokens and principals, UPDATE tokens.last_used_at; nothing else.
--   nacre_principal_admin  the admin CLI: insert principals / tokens / delegations / reviewer grants, revoke or disable
--                          them; a NOINHERIT member of nacre_app (SET LOCAL ROLE nacre_app to append the config_event
--                          in the same transaction, as keyadmin does, D-0014).
--   nacre_app              read-only: its OWN delegations (as the agent) and its OWN reviewer grants (as the person).
-- Tokens are stored only as a keyed hash (HMAC-SHA256 under a token-hashing key held outside the database); the
-- secret is never stored. Rows are append-only except the revocation / disable / last-used columns.

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nacre_auth') THEN
    CREATE ROLE nacre_auth NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nacre_principal_admin') THEN
    CREATE ROLE nacre_principal_admin NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT;
  END IF;
END
$$;
GRANT nacre_app TO nacre_principal_admin;

SET LOCAL ROLE nacre_migrator;

CREATE SCHEMA auth;
REVOKE ALL ON SCHEMA auth FROM PUBLIC;

CREATE TABLE auth.principals (
  principal_id     uuid         NOT NULL PRIMARY KEY,
  org_id           uuid         NOT NULL,
  kind             text         NOT NULL CHECK (kind IN ('agent', 'person', 'service', 'operator')),
  display_name     text         NOT NULL CHECK (length(display_name) BETWEEN 1 AND 200),
  service_sources  text[]       NOT NULL DEFAULT '{}' CHECK (service_sources <@ ARRAY['ci', 'review', 'git']::text[]),
  created_at       timestamptz  NOT NULL DEFAULT now(),
  disabled_at      timestamptz,
  config_event_id  uuid         NOT NULL,
  CHECK (kind = 'service' OR service_sources = '{}')
);

CREATE TABLE auth.tokens (
  token_id      uuid         NOT NULL PRIMARY KEY,
  principal_id  uuid         NOT NULL REFERENCES auth.principals (principal_id),
  token_mac     bytea        NOT NULL UNIQUE CHECK (octet_length(token_mac) = 32),
  created_at    timestamptz  NOT NULL DEFAULT now(),
  expires_at    timestamptz  NOT NULL,
  revoked_at    timestamptz,
  last_used_at  timestamptz,
  CHECK (expires_at > created_at AND expires_at <= created_at + interval '90 days')
);

CREATE TABLE auth.delegations (
  delegation_id     uuid         NOT NULL PRIMARY KEY,
  agent_principal   uuid         NOT NULL REFERENCES auth.principals (principal_id),
  person_principal  uuid         NOT NULL REFERENCES auth.principals (principal_id),
  streams           uuid[]       NOT NULL CHECK (cardinality(streams) >= 1),
  created_at        timestamptz  NOT NULL DEFAULT now(),
  expires_at        timestamptz  NOT NULL,
  revoked_at        timestamptz,
  config_event_id   uuid         NOT NULL,
  CHECK (expires_at > created_at AND expires_at <= created_at + interval '90 days')
);

CREATE TABLE auth.reviewer_grants (
  grant_id          uuid         NOT NULL PRIMARY KEY,
  person_principal  uuid         NOT NULL REFERENCES auth.principals (principal_id),
  stream_id         uuid         NOT NULL,
  created_at        timestamptz  NOT NULL DEFAULT now(),
  expires_at        timestamptz  NOT NULL,
  revoked_at        timestamptz,
  config_event_id   uuid         NOT NULL,
  CHECK (expires_at > created_at AND expires_at <= created_at + interval '90 days')
);

-- Kinds are enforced across tables: delegations go from an agent to a person; reviewer grants go to a person.
CREATE FUNCTION auth.check_delegation_kinds() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF (SELECT kind FROM auth.principals WHERE principal_id = NEW.agent_principal) IS DISTINCT FROM 'agent'
     OR (SELECT kind FROM auth.principals WHERE principal_id = NEW.person_principal) IS DISTINCT FROM 'person' THEN
    RAISE EXCEPTION 'auth: a delegation goes from an agent principal to a person principal';
  END IF;
  RETURN NEW;
END $$;
CREATE FUNCTION auth.check_reviewer_kind() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF (SELECT kind FROM auth.principals WHERE principal_id = NEW.person_principal) IS DISTINCT FROM 'person' THEN
    RAISE EXCEPTION 'auth: reviewer grants are given to person principals only';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER delegations_kinds BEFORE INSERT ON auth.delegations FOR EACH ROW EXECUTE FUNCTION auth.check_delegation_kinds();
CREATE TRIGGER reviewer_grants_kinds BEFORE INSERT ON auth.reviewer_grants FOR EACH ROW EXECUTE FUNCTION auth.check_reviewer_kind();
CREATE TRIGGER principals_no_delete BEFORE DELETE ON auth.principals FOR EACH ROW EXECUTE FUNCTION ledger.reject_mutation();
CREATE TRIGGER tokens_no_delete BEFORE DELETE ON auth.tokens FOR EACH ROW EXECUTE FUNCTION ledger.reject_mutation();
CREATE TRIGGER delegations_no_delete BEFORE DELETE ON auth.delegations FOR EACH ROW EXECUTE FUNCTION ledger.reject_mutation();
CREATE TRIGGER reviewer_grants_no_delete BEFORE DELETE ON auth.reviewer_grants FOR EACH ROW EXECUTE FUNCTION ledger.reject_mutation();

ALTER TABLE auth.principals ENABLE ROW LEVEL SECURITY;
ALTER TABLE auth.principals FORCE ROW LEVEL SECURITY;
ALTER TABLE auth.tokens ENABLE ROW LEVEL SECURITY;
ALTER TABLE auth.tokens FORCE ROW LEVEL SECURITY;
ALTER TABLE auth.delegations ENABLE ROW LEVEL SECURITY;
ALTER TABLE auth.delegations FORCE ROW LEVEL SECURITY;
ALTER TABLE auth.reviewer_grants ENABLE ROW LEVEL SECURITY;
ALTER TABLE auth.reviewer_grants FORCE ROW LEVEL SECURITY;

CREATE POLICY principals_auth ON auth.principals FOR SELECT TO nacre_auth USING (true);
CREATE POLICY tokens_auth_read ON auth.tokens FOR SELECT TO nacre_auth USING (true);
CREATE POLICY tokens_auth_stamp ON auth.tokens FOR UPDATE TO nacre_auth USING (true) WITH CHECK (true);
CREATE POLICY principals_admin ON auth.principals FOR ALL TO nacre_principal_admin USING (true) WITH CHECK (true);
CREATE POLICY tokens_admin ON auth.tokens FOR ALL TO nacre_principal_admin USING (true) WITH CHECK (true);
CREATE POLICY delegations_admin ON auth.delegations FOR ALL TO nacre_principal_admin USING (true) WITH CHECK (true);
CREATE POLICY reviewer_grants_admin ON auth.reviewer_grants FOR ALL TO nacre_principal_admin USING (true) WITH CHECK (true);
CREATE POLICY delegations_app_own ON auth.delegations FOR SELECT TO nacre_app
  USING (agent_principal = scopes.current_principal());
CREATE POLICY reviewer_grants_app_own ON auth.reviewer_grants FOR SELECT TO nacre_app
  USING (person_principal = scopes.current_principal());

GRANT USAGE ON SCHEMA auth TO nacre_auth, nacre_principal_admin, nacre_app;
GRANT SELECT ON auth.principals, auth.tokens TO nacre_auth;
GRANT UPDATE (last_used_at) ON auth.tokens TO nacre_auth;
GRANT SELECT, INSERT ON auth.principals, auth.tokens, auth.delegations, auth.reviewer_grants TO nacre_principal_admin;
GRANT UPDATE (disabled_at) ON auth.principals TO nacre_principal_admin;
GRANT UPDATE (revoked_at) ON auth.tokens, auth.delegations, auth.reviewer_grants TO nacre_principal_admin;
GRANT SELECT ON auth.delegations, auth.reviewer_grants TO nacre_app;
GRANT USAGE ON SCHEMA scopes TO nacre_principal_admin;
GRANT SELECT ON scopes.scopes TO nacre_principal_admin;
CREATE POLICY scopes_principal_admin_read ON scopes.scopes FOR SELECT TO nacre_principal_admin USING (true);
