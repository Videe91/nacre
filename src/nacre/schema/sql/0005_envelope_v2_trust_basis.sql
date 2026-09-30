-- 0005_envelope_v2_trust_basis: envelope v2 adds trust_basis (D-0002 amendment 4, D-0012 amendment 2).
-- Fix-forward: 0001's `CHECK (envelope_version = 1)` is replaced, not edited. v1 rows (none were ever written)
-- keep a NULL trust_basis; v2 rows must have one.

SET LOCAL ROLE nacre_migrator;

ALTER TABLE ledger.events ADD COLUMN trust_basis text CHECK (trust_basis IN ('asserted', 'verified'));
ALTER TABLE ledger.events DROP CONSTRAINT events_envelope_version_check;
ALTER TABLE ledger.events ADD CONSTRAINT events_envelope_version_check CHECK (envelope_version IN (1, 2));
ALTER TABLE ledger.events ADD CONSTRAINT events_trust_basis_by_version
  CHECK ((envelope_version = 1) = (trust_basis IS NULL));
