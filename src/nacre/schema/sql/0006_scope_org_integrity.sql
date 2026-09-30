-- 0006_scope_org_integrity: a grant names a stream of the SAME org; a parent scope is in the same org (D-0005).
-- Tightening only: composite foreign keys. Foreign-key checks run regardless of RLS.

SET LOCAL ROLE nacre_migrator;

ALTER TABLE scopes.scopes ADD CONSTRAINT scopes_stream_org_unique UNIQUE (stream_id, org_id);
ALTER TABLE scopes.scope_grants ADD CONSTRAINT scope_grants_stream_same_org
  FOREIGN KEY (stream_id, org_id) REFERENCES scopes.scopes (stream_id, org_id);
ALTER TABLE scopes.scopes ADD CONSTRAINT scopes_parent_same_org
  FOREIGN KEY (parent_stream_id, org_id) REFERENCES scopes.scopes (stream_id, org_id);
