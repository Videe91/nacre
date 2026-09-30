# A-0010 holdout-generation log (D-0011 amendments 1, 5)

Which holdout produced each official number. A holdout is **official** until a rule writer has seen its
failures and written a fix; then it is **demoted** to working data and a fresh one is sealed first.

| Id | Seed | Contexts | Negatives | Sealed in | Chosen by | Status | Official numbers produced |
|---|---|---|---|---|---|---|---|
| H1 | 77031117 | 9 (TOML, XML, Markdown code block, Go, JS, X-Api-Key header, SQL, CLI flag, error message) | holdout half of committed negatives (137 files) + seeded synthetic | e084b24 | builder session (had seen rule code; contexts chosen before any Nacre rule existed) | **demoted 2026-09-30**: its JWT/sntryu_ failures were seen and are being fixed | ce5e315 evidence `A-0010-measurement-2026-09-30-holdout.md` (the numbers stand as H1 results; later numbers must not use H1) |
| H2 | 3671910561 | 16, chosen by the separate session (Java text block, raw HTTP POST without trailing newline, CI log, Python traceback, chat prose followed by a comma, Terraform HCL, shell prompt with `&&`, C# verbatim string, PHP array, Lua long bracket, Markdown table cell, HTTP GET query parameter, pytest assertion output, Ruby %q(), Kubernetes Secret block scalar, JSX template literal); documents embedded; every generator in every context | 150 fresh CPython 3.14.3 stdlib files (PSF) + seeded synthetic negatives | (the "seal H2" commit) | a **separate session that did not read detector code** (2026-09-30). The builder saw the context DESCRIPTIONS, not results, before writing the boundary fix; the fix follows the charset principle only | **official** | (pending: first measurement after the boundary fix) |
