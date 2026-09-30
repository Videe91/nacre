# Decisions (ADRs)

| ID | Title | Tier | Status |
|---|---|---|---|
| D-0001 | Decision-first development, one file per functionality | D3 | accepted |
| D-0002 | Event envelope (fields, plaintext/ciphertext split, canonical encoding) | D2 | accepted |
| D-0003 | Seal chain (tamper evidence and gapless sequencing) | D2 | accepted |
| D-0004 | Crypto-shredding key granularity and key custody | D3 | accepted |
| D-0005 | Scope isolation (streams, access resolution, database enforcement) | D3 | accepted |
| D-0006 | Phase 1 dependencies and test infrastructure | D2 | accepted |
| D-0007 | Secret detection — vendored gitleaks rules + entropy check | D2 | accepted |
| D-0008 | Encrypted body format (header, AEAD, deterministic CBOR) | D2 | accepted |
| D-0009 | Regex engine for secret detection — google-re2 | D2 | accepted |
| D-0010 | Pre-commit secret scan | D2 | accepted |
| D-0011 | Nacre supplementary detection rules | D2 | accepted |
| D-0012 | Trust by source and author, and the idempotency request MAC | D3/D2 | accepted |
| D-0013 | Attachment blob format and checkpoint signature format | D2 | accepted |
| D-0014 | Shredding authority, key-administration roles, and rotation mechanics | D3 | accepted |
| D-0015 | Collecting orphan attachment blobs safely (per-ref advisory lock, nacre_gc role) | D2 | accepted |
