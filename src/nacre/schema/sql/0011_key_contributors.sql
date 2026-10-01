-- 0011_key_contributors: D-0023 contributor-set keys. A derived key is an ordinary row of keys.data_keys (so D-0014
-- master/root rotation rewraps it unchanged); this table records which (subject, source month) pairs contributed to
-- it. Erasing a person destroys every key with that person as a member; forgetting month M destroys every derived
-- key in the stream with a member month in M. Rows cascade with their key.

SET LOCAL ROLE nacre_migrator;

CREATE TABLE keys.key_contributors (
  key_id            uuid     NOT NULL REFERENCES keys.data_keys (key_id) ON DELETE CASCADE,
  stream_id         uuid     NOT NULL,
  member_subject    uuid     NOT NULL,
  member_month      date     NOT NULL CHECK (member_month = date_trunc('month', member_month)::date),
  member_is_person  boolean  NOT NULL,
  PRIMARY KEY (key_id, member_subject, member_month),
  CHECK (member_is_person = (member_subject <> stream_id))
);
CREATE INDEX key_contributors_person ON keys.key_contributors (member_subject) WHERE member_is_person;
CREATE INDEX key_contributors_month ON keys.key_contributors (stream_id, member_month);

-- A key's member rows must belong to the key's own stream.
CREATE FUNCTION keys.check_contributor() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM keys.data_keys WHERE key_id = NEW.key_id AND stream_id = NEW.stream_id) THEN
    RAISE EXCEPTION 'key_contributors: key % does not belong to stream %', NEW.key_id, NEW.stream_id;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER key_contributors_check BEFORE INSERT ON keys.key_contributors
  FOR EACH ROW EXECUTE FUNCTION keys.check_contributor();
CREATE TRIGGER key_contributors_no_update BEFORE UPDATE ON keys.key_contributors
  FOR EACH ROW EXECUTE FUNCTION ledger.reject_mutation();

ALTER TABLE keys.key_contributors ENABLE ROW LEVEL SECURITY;
ALTER TABLE keys.key_contributors FORCE ROW LEVEL SECURITY;
CREATE POLICY key_contributors_app_read ON keys.key_contributors FOR SELECT TO nacre_app
  USING (stream_id = ANY (scopes.read_streams()));
CREATE POLICY key_contributors_app_create ON keys.key_contributors FOR INSERT TO nacre_app
  WITH CHECK (stream_id = ANY (scopes.write_streams()));
CREATE POLICY key_contributors_keyadmin ON keys.key_contributors FOR ALL TO nacre_keyadmin USING (true) WITH CHECK (true);

REVOKE ALL ON keys.key_contributors FROM PUBLIC;
GRANT SELECT, INSERT ON keys.key_contributors TO nacre_app;
GRANT SELECT, DELETE ON keys.key_contributors TO nacre_keyadmin;
