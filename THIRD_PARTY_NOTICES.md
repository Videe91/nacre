# Third-party notices

## gitleaks rule set
- **File:** `src/nacre/ledger/data/gitleaks-v8.30.1.toml` (vendored unmodified, D-0007)
- **Source:** https://github.com/gitleaks/gitleaks/blob/v8.30.1/config/gitleaks.toml
- **Version:** v8.30.1, commit `83d9cd684c87d95d656c1458ef04895a7f1cbd8e`
- **sha256:** `e163e53b9e7e8a8511e77271e2b323ed057759542a6d988258afe3a1fa329caf`
- **License:** MIT. Full text: `src/nacre/ledger/data/LICENSE-gitleaks-v8.30.1`

## Negative test corpus (committed third-party files, D-0007 amendments 2-3)
Unmodified excerpts (truncated at a line boundary to 16 KiB) of permissively licensed Python packages,
used only as secret-free test input. Per-package versions, licences and licence files:
`tests/ledger/secret_corpus/negatives/MANIFEST.json` and `negatives/<package>/LICENSES/`.
Packages: cbor2 (MIT), cffi (MIT-0), cryptography (Apache-2.0 OR BSD-3-Clause), google-re2 (BSD),
iniconfig (MIT), packaging (Apache-2.0 OR BSD-2-Clause), pip (MIT; `pip/_vendor` excluded), pluggy (MIT),
pycparser (BSD-3-Clause), Pygments (BSD-2-Clause), pytest (MIT), sortedcontainers (Apache-2.0).

## lodash 4.17.21 (minified)
- **File:** `tests/ledger/secret_corpus/negatives_external/lodash-4.17.21/lodash.min.js`, unmodified
- **Source:** cdnjs, integrity matched against its published SRI hash
- **License:** MIT, full text beside it (`LICENSE`), © OpenJS Foundation and other contributors

## CPython 3.14.3 standard library excerpts (holdout H2 negatives)
- **Files:** `tests/ledger/secret_corpus/negatives_holdout2/` — 150 `.py` files truncated at a line boundary to
  16 KiB, test directories excluded; provenance in its `MANIFEST.json`.
- **License:** PSF License (PSF-2.0); full text in `negatives_holdout2/LICENSES/LICENSE-PSF.txt`.
