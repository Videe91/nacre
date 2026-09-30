# A-0010 research notes: provider-documented credential formats

- **Date:** 2026-09-30
- **Status:** research notes, **not yet verified row by row**. They were gathered by a research
  agent from provider-owned sources only: docs, engineering blogs, changelogs, staff posts, and in
  Sentry's case its source code. **No scanner rule sets were used** (D-0007 amendment 2).
- **Verification rule:** each row is re-fetched and checked when its generator is written. The
  generator's docstring cites the URL it was checked against.
- Example tokens from provider docs are **deliberately not reproduced here**. They are secret-shaped,
  and would trip the pre-commit scan (D-0010) and GitHub push protection.

## Summary by documentation quality

| Quality | Providers / token types |
|---|---|
| **Fully specified** (prefix + length + structure documented) | GitHub classic family (`ghp_ gho_ ghu_ ghs_ ghr_`: 40 chars, CRC32 base62 checksum in the last 6); GitHub stateless `ghs_APPID_JWT` (~520); GitLab legacy `glpat-` (+20); PyPI (`pypi-` + ≥85 of `[A-Za-z0-9-_]`, macaroon); Heroku (`HRKU-`, 65); Supabase (`sb_secret_` 41, `sb_publishable_` 46, 8-char checksum); Cloudflare (`cfk_ cfut_ cfat_` + 40 alnum + CRC32; `cfast_` 54); Replicate (`r8_`, 40); Shopify (`shpat_ shpca_ shppa_ shpss_`, 38; changelog now 404, verify via archive); SendGrid (69 chars); Sentry (`sntryu_` + 64 hex; `sntrys_` base64-JSON + 43-char secret) |
| **Prefix documented, length not** | OpenAI `sk-admin-` (page fetch was blocked; search excerpt only); Anthropic `sk-ant-api03-` / `sk-ant-admin`; Hugging Face `hf_`; DigitalOcean `dop_v1_ doo_v1_ dor_v1_`; GitHub fine-grained `github_pat_`; npm `npm_` (CRC32 base62 checksum documented, length not); Docker Hub `dckr_pat_ dckr_oat_`; Vercel `vcp_` (+ `vci vca vcr vck`); Netlify `nfp nfc nfo nfu nfb` (staff post); Stripe `sk_live_ sk_test_ rk_ pk_ sk_org_ whsec_` (no length anywhere); Slack `xoxb- xoxp- xapp- xwfp- xoxe.`; Notion `ntn_` (legacy `secret_`); Google `AIza` (39 from an official example only); AWS `AKIA`/`ASIA` (API allows 16–128; 20 only from the example) and secret keys (40 from the example only); Azure storage keys (512-bit, so 88 base64 is arithmetic); Azure SAS (documented query structure); GCP service-account JSON (documented field set); Twilio SIDs `AC`/`SK` + 32 hex (identifiers, not secrets) |
| **No official format doc** | OpenAI `sk-proj-` / `sk-svcacct-`; Groq; Mistral; Azure Entra client secret (only a Microsoft Purview *scanner* definition, excluded by the independence rule); Atlassian / Bitbucket (explicitly "opaque"); Databricks `dapi`; Twilio auth token / API key secret; Discord bot token (shape from an official example only); Linear; Datadog application key; Okta ("do not assume a set structure") |

## Generic category: standards

| Shape | Standard |
|---|---|
| PKCS#8 PEM | RFC 7468 (64-char lines), RFC 5208 / 5958 |
| RSA PKCS#1 PEM | RFC 8017 App. A.1.2 (ASN.1); `RSA PRIVATE KEY` label per OpenSSL PEM docs |
| EC SEC1 PEM | RFC 5915 §3–4; SEC 1 v2 §C.4 |
| OpenSSH private key | openssh-portable `PROTOCOL.key`; armor and 70-char wrapping in `sshkey.c` / `sshbuf-misc.c` |
| PostgreSQL / MySQL / MongoDB URIs with passwords | libpq §32.1.1.2; MySQL 8.4 URI docs; MongoDB connection-string formats |
| Credentials in URLs | RFC 3986 §3.2.1 (userinfo) |
| JWT / JWS compact | RFC 7515 §7.1, RFC 7519 |
| `.env` assignments | No formal standard; Docker Compose ".env file syntax" is the most authoritative; the dialect is a D1 choice to record |

## Full per-row table
`A-0010-provider-formats-full-unverified.md`: 71 provider rows + 10 generic rows, with URLs,
official?/confidence columns and open items. It is research-agent output, sanitized of example
tokens, and **unverified** until each row is checked for its generator.
