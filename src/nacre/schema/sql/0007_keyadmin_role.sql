-- 0007_keyadmin_role: the key-administration role (D-0014). It may read the scope registry, and read, rewrap and
-- destroy keys; it can NEVER read events or checkpoints. It is a NOINHERIT member of nacre_app: it holds none of
-- the app's privileges unless it explicitly does SET LOCAL ROLE nacre_app inside a transaction. That lets one
-- transaction destroy keys AND append the audit event, so a crash can never leave a destruction unrecorded. (D1)

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nacre_keyadmin') THEN
    CREATE ROLE nacre_keyadmin NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT;
  END IF;
END
$$;
GRANT nacre_app TO nacre_keyadmin;

SET LOCAL ROLE nacre_migrator;

GRANT USAGE ON SCHEMA keys, scopes TO nacre_keyadmin;
GRANT SELECT, DELETE ON keys.stream_master_keys, keys.data_keys TO nacre_keyadmin;
GRANT UPDATE (wrapped_key, root_key_version) ON keys.stream_master_keys TO nacre_keyadmin;
GRANT UPDATE (wrapped_key) ON keys.data_keys TO nacre_keyadmin;
GRANT SELECT ON scopes.scopes, scopes.scope_grants TO nacre_keyadmin;
GRANT UPDATE (status) ON scopes.scopes TO nacre_keyadmin;

CREATE POLICY master_keys_keyadmin ON keys.stream_master_keys FOR ALL TO nacre_keyadmin USING (true) WITH CHECK (true);
CREATE POLICY data_keys_keyadmin ON keys.data_keys FOR ALL TO nacre_keyadmin USING (true) WITH CHECK (true);
CREATE POLICY scopes_keyadmin_read ON scopes.scopes FOR SELECT TO nacre_keyadmin USING (true);
CREATE POLICY scopes_keyadmin_status ON scopes.scopes FOR UPDATE TO nacre_keyadmin USING (true) WITH CHECK (true);
CREATE POLICY grants_keyadmin_read ON scopes.scope_grants FOR SELECT TO nacre_keyadmin USING (true);
