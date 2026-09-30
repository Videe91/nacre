# A-0010 holdout-generation log (D-0011 amendments 1, 5)

Which holdout produced each official number. A holdout is **official** until a rule writer has seen its
failures and written a fix; then it is **demoted** to working data and a fresh one is sealed first.

| Id | Seed | Contexts | Negatives | Sealed in | Chosen by | Status | Official numbers produced |
|---|---|---|---|---|---|---|---|
| H1 | 77031117 | 9 (TOML, XML, Markdown code block, Go, JS, X-Api-Key header, SQL, CLI flag, error message) | holdout half of committed negatives (137 files) + seeded synthetic | e084b24 | builder session (had seen rule code; contexts chosen before any Nacre rule existed) | **demoted 2026-09-30**: its JWT/sntryu_ failures were seen and are being fixed | ce5e315 evidence `A-0010-measurement-2026-09-30-holdout.md` (the numbers stand as H1 results; later numbers must not use H1) |
