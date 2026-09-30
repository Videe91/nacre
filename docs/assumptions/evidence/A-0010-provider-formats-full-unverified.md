<!-- UNVERIFIED research-agent output (2026-09-30), sanitized of example tokens. Each row is re-verified against its URL when its generator is written (D-0007 amendment 2). Not a source of truth until then. -->

# Provider-documented credential formats (researched 2026-09-30)

Sources are provider-owned only: official docs, engineering blogs, changelogs, staff forum posts, and in Sentry's case the provider's own source code. No gitleaks, trufflehog, GitGuardian, GitHub pattern-list or other scanner rule was used as a source. The one exception is flagged: the Azure Entra client secret row cites a Microsoft Purview scanner definition.

A length marked "(example only)" was counted from an official example value. Those lengths, and any lengths computed by arithmetic, are assumptions, not documented spec facts.

Complete example token and key values copied from provider docs have been removed. Each is replaced with a description in square brackets.

**Blocked check:** fetching OpenAI's admin-key reference page was blocked by the permission classifier. The OpenAI `sk-admin-` row rests on a search-result excerpt of that official page. No attempt was made to get around the block.

## AI

| provider | token type | prefix | length | charset | checksum/structure | source | official? | conf. | notes |
|---|---|---|---|---|---|---|---|---|---|
| OpenAI | Admin API key | `sk-admin-` | not documented | not documented | not documented | https://developers.openai.com/api/reference/resources/organization/subresources/admin_api_keys/ | yes (search excerpt only) | medium | The direct fetch was blocked. |
| OpenAI | Project / service-account / legacy | `sk-proj-`, `sk-svcacct-`, `sk-` | not documented | – | – | community.openai.com threads and third-party blogs only | **no** | low | The 56 and ~164 character figures are user observations. |
| Anthropic | API key | `sk-ant-api03-` (docs text: "`sk-ant-api...`") | not documented | – | – | https://platform.claude.com/docs/en/manage-claude/authentication | yes | high (prefix) | The docs do not say whether `03` is fixed. |
| Anthropic | Admin key | `sk-ant-admin` | not documented | – | – | https://platform.claude.com/docs/en/manage-claude/admin-api | yes | high for `sk-ant-admin`; low for `01-` | `sk-ant-admin01-` appears only in third-party pages. |
| Google / Gemini | API key | `AIza` | 39 (example only); no length stated in prose | example uses `[A-Za-z0-9_-]` | none documented | https://docs.cloud.google.com/docs/authentication/api-keys | yes (example only) | medium | [official example omitted: 39 chars, AIza prefix]. The "AIza + 35" rule comes from Microsoft Purview, not Google. |
| Google / Gemini | Auth key (default for new AI Studio keys since 2026-05-28) | `AQ.` (forums only) | not documented | – | – | https://ai.google.dev/gemini-api/docs/api-key (no prefix given) | **no** (prefix) | low | New Gemini keys will not all start with `AIza`. |
| Hugging Face | User access token | `hf_` | not documented | – | – | https://huggingface.co/docs/hub/security-tokens | yes | high (prefix) | `api_org_` is undocumented. |
| Groq | API key | `gsk_` | – | – | – | third-party only | **no** | low | |
| Mistral | API key | none documented | – | – | – | https://docs.mistral.ai/admin/identity-access/api-keys (no format given) | **no** | low | Unprefixed. |
| Replicate | API token | `r8_` | 40 total ("40-character strings that always start with `r8_`") | not documented | not documented | https://replicate.com/docs/topics/security/api-tokens ; https://replicate.com/changelog/2024-04-10-github-secret-scanning | yes | high | |

## Cloud

| provider | token type | prefix | length | charset | checksum/structure | source | official? | conf. | notes |
|---|---|---|---|---|---|---|---|---|---|
| AWS | Long-term access key ID | `AKIA` | API constraint 16–128; 20 (example only) | pattern `[\w]+` | 4-char type prefix | https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_identifiers.html#identifiers-prefixes ; https://docs.aws.amazon.com/IAM/latest/APIReference/API_AccessKey.html | yes | high (prefix); medium (20) | [official example omitted: 20 chars, AKIA prefix]. AWS says "Prefixes may vary based on when they were created." The length 20 is never stated. |
| AWS | STS temporary key ID | `ASIA` | 16–128 | `[\w]*` | – | https://docs.aws.amazon.com/STS/latest/APIReference/API_Credentials.html | yes | high (prefix) | The session token format is not documented. |
| AWS | Other ID prefixes (non-secret) | `ABIA ACCA AGPA AIDA AIPA ANPA ANVA APKA AROA ASCA` | – | – | – | IAM identifiers page | yes | high | Useful as near-miss negatives. |
| AWS | Secret access key | none | not documented; 40 (example only) | base64-like, includes `/` (example only) | – | API_AccessKey.html | partial | medium | [official example omitted: 40 chars, base64-like, contains `/`]. |
| GCP | API key | `AIza` | see the Google row | | | https://docs.cloud.google.com/docs/authentication/api-keys | yes (example) | medium | |
| GCP | Service-account key JSON | `"type": "service_account"` | – | – | Fields: `type, project_id, private_key_id, private_key` (PKCS#8 PEM `-----BEGIN PRIVATE KEY-----`), `client_email, client_id, auth_uri, token_uri, auth_provider_x509_cert_url, client_x509_cert_url`. RSA 2048 by default. | https://docs.cloud.google.com/iam/docs/keys-create-delete ; https://docs.cloud.google.com/iam/docs/service-account-overview | yes | high | `private_key_id` length is not documented. `client_email` has the form `name@project.iam.gserviceaccount.com`. |
| Azure | Storage account key | none | "two 512-bit … keys"; 88-char base64 is arithmetic, not a quote | base64 (implied) | – | https://learn.microsoft.com/en-us/azure/storage/common/storage-account-keys-manage | yes (bit size) | medium | |
| Azure | SAS token | query string | variable | URL query | Params `sv sr sp st se sip spr si … sig`. `sig` = Base64(HMAC-SHA256). | https://learn.microsoft.com/en-us/rest/api/storageservices/create-service-sas | yes | high | The 44-char `sig` before URL-encoding is derived, not stated. |
| Azure | Entra client secret | no prefix documented (`Q~` in positions 4–5 is folklore) | "up to 40" | `a-z0-9-_.~` | "checksum: yes" | https://learn.microsoft.com/en-us/purview/sit-defn-azure-ad-client-secret | Microsoft, but **a Purview scanner definition** | medium-low | Excluded by the brief; flagged only. |
| Microsoft | CASK identifiable keys | `JQQJ` signature, plus a provider signature (e.g. `AZDO`) | 52 random chars plus more | base62 | checksum present; algorithm not given | https://devblogs.microsoft.com/engineering-at-microsoft/common-annotated-security-keys/ | yes (announcement) | low-medium | Which services issue CASK keys is not stated. |
| Cloudflare | Global API key | `cfk_` | 4 + 40 + checksum | 40 alphanumeric | CRC32 suffix; length not stated | https://developers.cloudflare.com/fundamentals/api/get-started/token-formats/ ; https://developers.cloudflare.com/changelog/post/2026-04-14-cloudflare-api-token-detections/ | yes | high | Old format: 37–45 lowercase hex, unprefixed. The changelog calls it "User API Key". |
| Cloudflare | User API token | `cfut_` | 5 + 40 + checksum | alphanumeric | CRC32 | same | yes | high | Old format: 40 alphanumeric, unprefixed. |
| Cloudflare | Account API token | `cfat_` | 5 + 40 + checksum | alphanumeric | CRC32 | same | yes | high | |
| Cloudflare | Access service-token client secret | `cfast_` | 54 (6 + 40 + 8) | alphanumeric | "8-character checksum" | https://developers.cloudflare.com/changelog/post/2026-08-26-service-token-secret-format/ | yes | high | The most complete spec found. |
| DigitalOcean | PAT / OAuth access / OAuth refresh | `dop_v1_` / `doo_v1_` / `dor_v1_` | not documented | – | – | https://docs.digitalocean.com/notes/2022/api-token-format ; https://digitalocean.com/blog/updated-api-tokens-new-management-features | yes | high (prefix) | |

## Code and packages

| provider | token type | prefix | length | charset | checksum/structure | source | official? | conf. | notes |
|---|---|---|---|---|---|---|---|---|---|
| GitHub | Classic PAT | `ghp_` | 40 ("length … remaining the same"); 4 + 30 random + 6 checksum | `[A-Za-z0-9_]` | "32 bit checksum in the last 6 digits", CRC32, base62, zero-padded | https://github.blog/engineering/platform-security/behind-githubs-new-authentication-token-formats/ ; https://github.blog/changelog/2021-03-31-authentication-token-format-updates-are-generally-available/ ; https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/about-authentication-to-github | yes | high; medium on exactly 40 | GitHub says to support up to 255 chars. |
| GitHub | OAuth / user-to-server / refresh | `gho_` / `ghu_` / `ghr_` | 40 (same basis as `ghp_`); `ghr_` length not stated | same | CRC32 + base62 | same | yes | medium (length) | |
| GitHub | Installation token (legacy) | `ghs_` | 40 | same | same | docs page above | yes | high | |
| GitHub | Installation token (stateless, rollout from 2026-04-27) | `ghs_` | ~520, variable | JWT chars + `_` | `ghs_APPID_JWT`, signed internally by GitHub | https://github.blog/changelog/2026-04-24-notice-about-upcoming-new-format-for-github-app-installation-tokens/ ; https://github.blog/changelog/2026-05-15-github-app-installation-tokens-per-request-override-header | yes | high | Also applies to the Actions `GITHUB_TOKEN`. |
| GitHub | Fine-grained PAT | `github_pat_` | not documented | – | not documented | about-authentication page | yes (prefix) | high (prefix) | |
| GitLab | Personal / project / group / impersonation tokens | `glpat-` (configurable per instance) | Legacy: prefix + 20 = 26 ("token must be 20 characters long"). Routable: 43–316. | Routable: base64 payload, `.`, base36 | Routable: `<prefix><b64payload>.<2-char base36 version>.<2-char base36 payload length><7-char base36 CRC32>` | https://docs.gitlab.com/security/tokens/ ; https://docs.gitlab.com/user/profile/personal_access_tokens/ ; https://handbook.gitlab.com/handbook/engineering/architecture/design-documents/cells/routable_tokens/ ; https://gitlab.com/gitlab-org/gitlab/-/merge_requests/169322 | yes (the routable spec is a *proposed* design doc) | high (prefix); medium (routable) | Behind feature flag `routable_pat`. Default-on version unconfirmed. [handbook example omitted: 40-byte minimal routable token]. |
| GitLab | Other token types | `gloas-` `gldt-` `glrt-` `glrtr-` `glcbt-` `glptt-` `glft-` `glimt-` `glagent-` `glwt-` `glsoat-` `glffct-` (plus cookie name `_gitlab_session=`) | not documented | – | – | https://docs.gitlab.com/security/tokens/ | yes | high (prefix) | |
| Bitbucket / Atlassian | API tokens, access tokens, app passwords | none documented | "varied API token length" | – | – | https://support.atlassian.com/atlassian-account/docs/manage-api-tokens-for-your-atlassian-account/ ; https://community.developer.atlassian.com/t/about-the-format-of-atlassian-security-tokens/62553 ; https://jira.atlassian.com/browse/ID-8131 | **no format doc** | high that none exists | Atlassian staff call tokens "opaque". `ATATT`/`ATCTT` come from scanners only. App passwords are removed from 2026-07-28. |
| npm | Access token (granular) | `npm_` | not documented (old format was a 36-char UUID) | "larger alphabet" | "last six characters … CRC32 … Base62"; 178-bit entropy | https://github.blog/security/announcing-npms-new-access-token-format/ ; https://github.blog/changelog/2021-09-23-npm-has-a-new-access-token-format ; https://docs.npmjs.com/about-access-tokens | yes | high (prefix, checksum); low (length) | The npm docs still say "hexadecimal string", which is stale. |
| PyPI | API token | `pypi-` | ≥ 90 total (base64 part "not shorter than 85"); no upper bound because of caveats | PyPI's own regex: `pypi-[A-Za-z0-9-_]{85,}` | Macaroon (identifier + caveats + HMAC chain) | https://docs.pypi.org/api/secrets/ ; https://pypi.org/help/ | yes | high | Used with username `__token__`. |
| Docker Hub | Personal access token | `dckr_pat_` | not documented | – | – | https://docs.docker.com/reference/api/hub/latest/operations/AuthCreateAccessToken.md | partial (example only) | medium | [official example omitted: placeholder value with dckr_pat_ prefix]. |
| Docker Hub | Organization access token | `dckr_oat_` | not documented | – | – | https://docs.docker.com/reference/api/hub/latest/schemas/createOrgAccessTokenResponse/ | partial | medium | [official example omitted: dckr_oat_ prefix + 28 alphanumeric]. |

## Hosting and databases

| provider | token type | prefix | length | charset | checksum/structure | source | official? | conf. | notes |
|---|---|---|---|---|---|---|---|---|---|
| Vercel | Personal access token | `vcp_` | not documented | – | – | https://vercel.com/docs/accounts/access-tokens ; https://vercel.com/changelog/new-token-formats-and-secret-scanning | yes | high | |
| Vercel | Integration / app access / app refresh / API key | `vci` `vca` `vcr` `vck` | not documented | – | – | changelog above | yes | medium | The `_` separator is confirmed only for `vcp_`. |
| Netlify | PAT / CLI / OAuth / app / build tokens | `nfp` `nfc` `nfo` `nfu` `nfb` (no underscore stated) | only a "40 characters" storage hint | – | – | https://answers.netlify.com/t/change-to-the-netlify-authentication-token-format/106146 (staff announcement) | yes (staff forum, not docs) | medium | |
| Heroku | OAuth token / API key (since 2024) | `HRKU-` | 65 ("65 characters long and prefixed with `HRKU-`") | not stated; the example contains `_` | not documented | https://devcenter.heroku.com/articles/oauth ; https://devcenter.heroku.com/changelog-items/2800 ; https://devcenter.heroku.com/changelog-items/2842 | yes | high | [official example omitted: 65 chars, HRKU- prefix + 60 chars incl. `_`]. Changelog 2842 earlier showed `HRKU-` + UUID (41). Legacy tokens are bare UUIDs. |
| Supabase | Publishable key | `sb_publishable_` | 46 (prefix + 22 + `_` + 8) | base64url-like (example only) | 8-char checksum; algorithm not documented | https://supabase.com/docs/guides/api/api-keys ; https://supabase.com/docs/guides/self-hosting/self-hosted-auth-keys.md ; https://github.com/orgs/supabase/discussions/29260 | yes | high / medium (charset) | [official example omitted: 46 chars, sb_publishable_ prefix]. Safe to expose, so arguably not a secret. |
| Supabase | Secret key | `sb_secret_` | 41 | base64url-like, includes `-` (example only) | same | same | yes | high | [official example omitted: 41 chars, sb_secret_ prefix]. |
| Supabase | Legacy `anon` / `service_role` | `eyJ` (JWT) | not documented | JWT | HS-signed JWT; only the `role` claim differs | https://supabase.com/docs/guides/api/api-keys | yes | high | Deprecated by end of 2026. |
| Databricks | PAT | `dapi` (**not in official docs**) | – | – | – | https://docs.databricks.com/aws/en/dev-tools/auth/pat ; https://learn.microsoft.com/en-us/azure/databricks/dev-tools/auth/pat (checked: no format given) | **no** | – | `dapi` appears only in scanner and third-party docs. |

## Payments and commerce

| provider | token type | prefix | length | charset | checksum/structure | source | official? | conf. | notes |
|---|---|---|---|---|---|---|---|---|---|
| Stripe | Secret key | `sk_live_` / `sk_test_` | **not documented** | – | – | https://docs.stripe.com/keys | yes | high (prefix) | No official "up to 255 chars" statement exists for API keys (that limit is for idempotency keys). https://support.stripe.com/questions/new-api-keys-are-longer-than-existing-keys gives no number. |
| Stripe | Publishable key | `pk_live_` / `pk_test_` | not documented | – | – | same | yes | high | Public, so a benign/negative case. |
| Stripe | Restricted key | `rk_live_` / `rk_test_` | not documented | – | – | same | yes | high | |
| Stripe | Organization key | `sk_org_` | not documented | – | – | same | yes | high | |
| Stripe | Webhook signing secret | `whsec_` | not documented | – | HMAC-SHA256 key | https://docs.stripe.com/webhooks/signature | yes | high | |
| Shopify | Admin API token (public / custom / legacy private app) | `shpat_` / `shpca_` / `shppa_` | 38 (prefix + 32); legacy tokens are 32, unprefixed | not documented | – | https://shopify.dev/changelog/length-of-the-shopify-access-token-is-increasing | yes | medium-high | **The URL now returns 404.** The text was read from a search-engine copy. |
| Shopify | App secret key | `shpss_` | 38 (was 32) | not documented | – | https://shopify.dev/changelog/app-secret-key-length-has-increased | yes | medium-high | Same 404 caveat. |

## Messaging

| provider | token type | prefix | length | charset | checksum/structure | source | official? | conf. | notes |
|---|---|---|---|---|---|---|---|---|---|
| Slack | Bot / user / app-level / workflow | `xoxb-` / `xoxp-` / `xapp-` / `xwfp-` | not documented. The last user-token segment is 32 chars (older tokens 6 or 10). | the example secret looks hex | hyphen-separated; "final section is the secret" | https://docs.slack.dev/authentication/tokens | yes | high (prefix) | [official example omitted: xoxp- + three numeric segments + 32-char hex-looking secret]. |
| Slack | Rotating access token | `xoxe.xoxb-` / `xoxe.xoxp-` | not documented | – | `xoxe.` wraps the normal prefix | https://docs.slack.dev/authentication/using-token-rotation | yes | high | |
| Slack | Refresh token | `xoxe-` | not documented | – | – | same ; https://docs.slack.dev/reference/methods/tooling.tokens.rotate | yes | high | |
| Slack | App config token | `xoxe.xoxp-` | not documented | – | expires after 12h | tooling.tokens.rotate | yes | high | |
| Slack | Incoming webhook | `https://hooks.slack.com/services/` | not documented | – | `/T…/B…/<secret>`; the placeholder shows T+8, B+8, 24-char secret | https://docs.slack.dev/messaging/sending-messages-using-incoming-webhooks | yes | high (shape); low (segment lengths) | |
| Twilio | Account SID / API key SID | `AC` / `SK` | 34 | 32 hex after the prefix | 2-letter type prefix | https://www.twilio.com/docs/glossary/what-is-a-sid ; https://www.twilio.com/docs/iam/api-keys/key-resource-v1 | yes | high | Identifiers, not secrets. |
| Twilio | Auth token / API key secret | none | **not documented** | – | – | https://www.twilio.com/docs/iam/api/authtoken | **no** | low | "32 hex" comes only from third parties and scanners. |
| SendGrid | API key | `SG.` | 69 ("always 69 characters long") | not documented | two dot-separated parts: **not officially stated** | https://support.sendgrid.com/hc/en-us/articles/44146758703387 ; https://support.twilio.com/hc/en-us/articles/47305226780059 | length yes; prefix only in Twilio's own agent skill (https://www.skills.sh/twilio/ai/twilio-sendgrid-account-setup) | high (length); medium (prefix) | |
| Discord | Bot token | none | not documented | – | 3 dot-separated parts (example only); the first part base64-decodes to a user snowflake | https://docs.discord.com/developers/reference | example only | medium (shape) | [official example omitted: 3 dot-separated base64url segments, 24 + 6 + 27 chars]. The "timestamp.HMAC" meaning is third-party only. |
| Discord | Webhook URL | `https://discord.com/api/webhooks/` | not documented | the id is a numeric snowflake | `/webhooks/{id}/{token}` | https://docs.discord.com/developers/resources/webhook | yes (route) | high (route) | Token charset and length are not stated. |

## Work tools and monitoring

| provider | token type | prefix | length | charset | checksum/structure | source | official? | conf. | notes |
|---|---|---|---|---|---|---|---|---|---|
| Atlassian | Account API token | `ATAT` (Bamboo Javadoc: "identified by the prefix 'ATAT'") | "varied"; about 192 implied by ID-8131 | not documented | – | https://docs.atlassian.com/atlassian-bamboo/11.0.7/com/atlassian/bamboo/repository/configuration/BitbucketCloudApiTokenUtils.html ; https://support.atlassian.com/atlassian-account/docs/manage-api-tokens-for-your-atlassian-account/ ; https://jira.atlassian.com/browse/ID-8131 | partial | medium | The longer `ATATT…` prefix comes from third parties only. Atlassian calls tokens opaque. |
| Linear | Personal API key | `lin_api_` (**unofficial**) | – | – | – | https://linear.app/developers/graphql (no prefix given) | **no** | low | |
| Notion | Integration token | `ntn_` (since 2024-09-25); legacy `secret_` | not documented | – | – | https://developers.notion.com/page/changelog | yes | high (prefix) | Notion advises treating tokens as opaque and not matching them with regex. |
| Datadog | API key | none | 32 (example only) | hex (example only) | – | https://docs.datadoghq.com/api/latest/key-management/create-an-api-key.md | example only | medium | [official example omitted: 32 lowercase hex chars]. |
| Datadog | Application key | none / `ddapp_` | – | – | – | https://docs.datadoghq.com/account_management/api-app-keys/ (no format given) | **no** | low | "40 hex" and `ddapp_` appear only in AWS Secrets Manager partner docs. |
| Sentry | Org auth token | `sntrys_` | variable | base64 | `sntrys_{base64(JSON{iat,url,region_url,org})}_{base64 of 32 random bytes, unpadded, 43 chars}` | https://raw.githubusercontent.com/getsentry/sentry/master/src/sentry/utils/security/orgauthtoken_token.py ; https://docs.sentry.io/account/auth-tokens/ | yes (provider source code) | high | The payload may contain `+/`. |
| Sentry | User auth token | `sntryu_` (also `sntrya_`, `sntryi_`) | 71 (7 + 64) | hex (`token_hex(32)`) | – | https://raw.githubusercontent.com/getsentry/sentry/master/src/sentry/types/token.py ; https://raw.githubusercontent.com/getsentry/sentry/master/src/sentry/models/apitoken.py | yes (source code) | high | Legacy tokens are 64 hex, unprefixed. |
| Sentry | DSN | `https://<publickey>@o<org>.ingest.sentry.io/<project>` | not documented | – | `{PROTOCOL}://{PUBLIC_KEY}[:{SECRET_KEY}]@{HOST}{PATH}/{PROJECT_ID}` | https://docs.sentry.io/concepts/key-terms/dsn-explainer/ | yes | high | "safe to keep public"; a benign case. |
| Okta | API token (SSWS) | `00` (observed only) | 42 (observed only) | – | – | https://devforum.okta.com/t/okta-api-token-format/25375 | **no** | low | Okta staff: "You should not assume a set structure." |

## Generic shapes (standards)

| shape | format | source | conf. | notes |
|---|---|---|---|---|
| PKCS#8 private key | `-----BEGIN PRIVATE KEY-----` (unencrypted) and `-----BEGIN ENCRYPTED PRIVATE KEY-----`. Base64 `[A-Za-z0-9+/=]`; generators MUST wrap at exactly 64 chars/line, with a shorter last line. | RFC 7468 §2, §3, §10, §11: https://www.rfc-editor.org/rfc/rfc7468 ; ASN.1 in RFC 5208 / RFC 5958 | high | RFC 7468 does **not** define the `RSA PRIVATE KEY` or `EC PRIVATE KEY` labels. |
| RSA PKCS#1 | ASN.1 `RSAPrivateKey`. PEM label `-----BEGIN RSA PRIVATE KEY-----` (OpenSSL "traditional" format). The encrypted variant adds `Proc-Type: 4,ENCRYPTED` and `DEK-Info: <cipher>,<hex IV>` headers. | ASN.1: RFC 8017 App. A.1.2 https://www.rfc-editor.org/rfc/rfc8017#appendix-A.1.2 ; label: OpenSSL PEM_read_bio_PrivateKey(3) "PEM ENCRYPTION FORMAT" https://docs.openssl.org/master/man3/PEM_read_bio_PrivateKey/ ; legacy headers: RFC 1421 | high | The label comes from OpenSSL, not an RFC. |
| EC SEC1 | `ECPrivateKey ::= SEQUENCE { version 1, privateKey OCTET STRING, [0] parameters, [1] publicKey }`; PEM `-----BEGIN EC PRIVATE KEY-----` | RFC 5915 §3–4 https://www.rfc-editor.org/rfc/rfc5915 ; SEC 1 v2 §C.4 https://www.secg.org/sec1-v2.pdf | high | RFC 5915 §4 names the label. |
| OpenSSH private key | Armor `-----BEGIN OPENSSH PRIVATE KEY-----`. The body is base64 of: `"openssh-key-v1\0"`, ciphername, kdfname, kdfoptions, uint32 N, N public keys, then the encrypted private block (two matching checkints, keys and comments, pad bytes 1,2,3…). Unencrypted keys use cipher and KDF `"none"`. | https://github.com/openssh/openssh-portable/blob/master/PROTOCOL.key ; armor and wrap: sshkey.c (MARK_BEGIN) and sshbuf-misc.c (sshbuf_dtob64) in the same repo | high | PROTOCOL.key does not define the armor. OpenSSH source wraps lines at **70** chars. The magic includes the NUL, so the body starts `b3BlbnNzaC1rZXktdjE` (derived). |
| PostgreSQL URI | `postgresql://[userspec@][hostspec][/dbname][?paramspec]`, where userspec = `user[:password]`. Scheme is `postgresql://` or `postgres://`. Comma-separated multi-host is allowed. Percent-encoding is required. | libpq docs §32.1.1.2 https://www.postgresql.org/docs/current/libpq-connect.html#LIBPQ-CONNSTRING-URIS | high | |
| MySQL URI | `[scheme://][user[:[password]]@]host[:port][/schema][?attr=val&…]`. Scheme is `mysql://` or `mysqlx://`. `@` is encoded as `%40`. | https://dev.mysql.com/doc/refman/8.4/en/connecting-using-uri-or-key-value-pairs.html | high | The doc warns that explicit passwords are insecure. |
| MongoDB | `mongodb://[username:password@]host1[:port1][,...hostN[:portN]][/[defaultauthdb][?options]]` and `mongodb+srv://[username:password@]host[/[defaultauthdb][?options]]` (SRV: one host, no port) | https://www.mongodb.com/docs/manual/reference/connection-string-formats/ | high | Credentials must be percent-encoded. |
| Credentials in URL | `userinfo = *( unreserved / pct-encoded / sub-delims / ":" )` before `@` in the authority | RFC 3986 §3.2.1 https://www.rfc-editor.org/rfc/rfc3986#section-3.2.1 | high | `user:password` in userinfo is deprecated but still parses. |
| JWT / JWS compact | `BASE64URL(header) '.' BASE64URL(payload) '.' BASE64URL(signature)`. Base64url without padding. Tokens usually start `eyJ`. An unsecured JWT (`alg: none`) has an empty third segment. | RFC 7515 §7.1 https://www.rfc-editor.org/rfc/rfc7515#section-7.1 (base64url: §2) ; RFC 7519 §3, §6 https://www.rfc-editor.org/rfc/rfc7519 | high | The `eyJ` start is derived. |
| .env assignments | There is no formal standard; the most authoritative spec is Docker Compose ".env file syntax". Rules: `#` comments; blank lines ignored; `KEY=VAL` (or `:`); values may be unquoted, single-quoted (literal, may be multiline) or double-quoted (interpolation, with `\n \r \t \\` escapes); inline comments need a preceding space. | https://docs.docker.com/compose/how-tos/environment-variables/variable-interpolation/#env-file-syntax ; de facto: https://github.com/motdotla/dotenv#readme ; `export KEY=val` follows POSIX shell (IEEE 1003.1 Shell Command Language §2.9.1 and the `export` builtin) | medium | Parsers differ; record the chosen dialect as a decision. |

## Providers lacking official format docs, and substitutes

- **OpenAI** (`sk-proj-`, `sk-svcacct-`, `sk-`): no prefix or length doc. Only `sk-admin-` is official, and it was not read directly. Substitute: **Replicate** (`r8_`, 40).
- **Groq** (`gsk_`): nothing official. Substitute: **Replicate**.
- **Mistral**: no prefix and no format. Substitute: **Replicate**.
- **Google `AQ.` auth keys**: prefix is on forums only. Keep `AIza`.
- **Hugging Face `api_org_`**: undocumented. Use `hf_` only.
- **Azure Entra client secret**: only a Purview scanner definition exists. Substitute: **Cloudflare `cfast_`** (54, 8-char checksum).
- **Atlassian / Bitbucket**: tokens are explicitly opaque; the longer `ATATT…` / `ATCTT…` prefixes are scanner-only. Substitute: **GitLab** (official prefix table plus the routable CRC32 structure) or **GitHub** classic tokens.
- **Databricks** (`dapi`): not in official docs. Substitute: **Heroku** (`HRKU-`, 65) or **Supabase** (`sb_secret_`, 41).
- **Twilio auth token / API key secret**: undocumented. Substitute: Twilio SIDs as identifier cases, or **SendGrid** (69).
- **Discord bot token structure**: undocumented. Substitute: Slack prefixes, or keep Discord as a shape-only case.
- **Linear** (`lin_api_`): unofficial. Substitute: **Notion** `ntn_` or **Sentry** `sntryu_` (fully specified).
- **Datadog application key / `ddapp_`**: undocumented. Substitute: **Sentry**.
- **Okta**: format explicitly disclaimed. Substitute: **Sentry**.
- **Lengths not documented** (treat as assumptions if used): Stripe (all), GitHub `github_pat_`, npm, Docker Hub, Vercel, DigitalOcean, Anthropic, Hugging Face, and Slack (except the 32-char user-token secret). AWS 20/40 comes from examples only; the API allows 16–128 for key IDs.

## Open items to verify before recording as evidence

1. **Shopify**: the changelog URLs now return 404. Check the quoted text against an archive.
2. **GitLab**: which version enables routable PATs by default (flag `routable_pat`) is unconfirmed.
3. **Vercel**: whether `vci`, `vca`, `vcr` and `vck` use a `_` separator is unconfirmed.
4. **OpenAI**: the `sk-admin-` page could not be read directly because the fetch was blocked by the permission classifier.
5. **Cloudflare**: the checksum length for `cfk_`, `cfut_` and `cfat_` is not stated.
