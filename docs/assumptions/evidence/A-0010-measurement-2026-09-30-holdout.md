# A-0010 measurement with Nacre rules, working vs sealed holdout (2026-09-30)

- Working corpus sha256 `686f675007f4f7920f22f5c335e91841b35aac5679967d2b171f06a2ce64b175`; holdout `e138000609a639e7456c0adba676a49bf17a111af02d64c94ed01b578feb2345` (sealed in commit e084b24,
  before any Nacre rule was written; own seed, 9 contexts unseen by the working set).
- Detector: gitleaks v8.30.1 + nacre-rules-v1 (5 rules, D-0011) + public layer + entropy layer.
- **Official rates are the holdout's** (D-0011 amendment 1). A working rate above the holdout rate by
  more than 1 point is flagged as overfitting.

| Group | Working | Holdout (official) | Target ≥ 99% | Overfitting flag |
|---|---|---|---|---|
| generic:credentials-in-url/https-userinfo | 100.0% | 100.0% | meets |  |
| generic:db-connection-string/mongodb | 100.0% | 100.0% | meets |  |
| generic:db-connection-string/mysql | 100.0% | 100.0% | meets |  |
| generic:db-connection-string/postgresql | 100.0% | 100.0% | meets |  |
| generic:dotenv/assignment | 100.0% | 100.0% | meets |  |
| generic:jwt/hs256 | 100.0% | 78.0% | **below** | **yes** |
| generic:private-key/openssh-ed25519 | 100.0% | 100.0% | meets |  |
| generic:private-key/openssh-rsa | 100.0% | 100.0% | meets |  |
| generic:private-key/pkcs1-rsa | 100.0% | 100.0% | meets |  |
| generic:private-key/pkcs8-ec | 100.0% | 100.0% | meets |  |
| generic:private-key/pkcs8-rsa | 100.0% | 100.0% | meets |  |
| generic:private-key/sec1-ec | 100.0% | 100.0% | meets |  |
| provider:GitHub | 100.0% | 100.0% | meets |  |
| provider:GitLab | 100.0% | 100.0% | meets |  |
| provider:Heroku | 100.0% | 100.0% | meets |  |
| provider:PyPI | 100.0% | 100.0% | meets |  |
| provider:Sentry | 100.0% | 92.7% | **below** | **yes** |
| provider:Supabase | 100.0% | 89.0% | **below** | **yes** |

| Public (must survive) | Working | Holdout |
|---|---|---|
| public:Sentry/DSN (public key only) | 100.0% | 100.0% |
| public:Supabase/legacy anon JWT | 100.0% | 100.0% |
| public:Supabase/sb_publishable | 100.0% | 100.0% |

- **False positives:** working 0.24% (1/424), **holdout 0.52% (2/387)**; holdout FP documents: [('synthetic/base64-blob', ['entropy']), ('negatives/packaging/packaging/licenses/_spdx.py', ['entropy'])].
- **Below target on the holdout:** JWT (hs256), Supabase service_role JWT, Sentry sntryu_, each at 78%. The
  vendored gitleaks `jwt` and `sentry-user-token` rules require a quote, whitespace or `;` after the token,
  so they miss the holdout's XML (`</credential>`) and error-message (`…: 401`) contexts. The working set
  showed 100%: the overfitting the holdout exists to catch.
- **All 5 Nacre rules: 100% on the holdout** for every kind they target.
- Coverage: 12/221 gitleaks rules caught a covered sample.
