<!-- Research-agent output (2026-09-30): every fact quoted from provider-owned sources, pinned to commits. Example tokens described, never copied. Spot-checked by the builder: GitLab routable_token.rb and Sentry projectkey.py were re-fetched and read directly. -->

# Credential-format verification: provider-owned sources only

Research date: 2026-09-30. Every quote below was fetched live on this date: pages with curl, repos with `gh api` or a shallow `git clone`. Code links are pinned to the commit SHA that was read.
Sources: provider docs, provider engineering blogs and changelogs, and repos owned by the provider's GitHub or GitLab org. No scanner rule sets and no third-party blogs were used. Where a provider's own repo includes a scanner-style list covering *other* providers (Vercel CLI, Shopify CLI), I flag it and do not use it as a source for those other providers.
Example tokens are never copied. They are described instead.

Legend: **CONFIRMED** means the source states the fact. **NOT FOUND** means the source does not state it. **CONTRADICTED** means the source states something different. **INFERRED** means the fact is derived from stated facts or from examples, and is not stated directly.

---

## PART 1: Verification of cited rows

### 1. GitHub classic tokens (ghp_, gho_, ghu_, ghs_, ghr_)

Sources fetched:
- A. https://github.blog/engineering/platform-security/behind-githubs-new-authentication-token-formats/ (published 2021-04-05)
- B. https://github.blog/changelog/2021-03-31-authentication-token-format-updates-are-generally-available/
- C. https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/about-authentication-to-github
- D. https://github.blog/changelog/2021-03-04-authentication-token-format-updates/
- E. https://github.blog/changelog/2026-05-15-github-app-installation-tokens-per-request-override-header

| Fact | Status | Quote and source |
|---|---|---|
| Prefixes ghp/gho/ghu/ghs/ghr, separator `_` | CONFIRMED | A: "We are including specific 3 letter prefixes … ghp for GitHub personal access tokens, gho for OAuth access tokens, ghu … ghs … ghr for refresh tokens" and "we are adding a separator: _ ." B lists `ghp_`, `gho_`, `ghu_`, `ghs_`, `ghr_`. C's table adds `github_pat_` for fine-grained PATs. |
| Charset | CONFIRMED (B) | B: "The character set changed from [a-f0-9] to [A-Za-z0-9_]". Note that this charset includes `_` because it describes the whole token including the separator. A's entropy line uses a 62-symbol alphabet for the random part: `(("a".."z") + ("A".."Z") + (0..9)).length … * 30 = 178`. That means the random part is 30 chars of [a-zA-Z0-9]. |
| Total length = 40 | INFERRED, not stated as a number | A: "all without changing the token length". The old format is described in A as "hex-encoded 40 character strings". B: "The length of our tokens is remaining the same for now." C (2026 note) calls the classic installation tokens "exactly 40 characters long". The arithmetic 4 (prefix+`_`) + 30 (random) + 6 (checksum) = 40 is consistent with this, but no source states the 4+30+6 split explicitly. |
| Checksum = 32-bit, last 6 chars | CONFIRMED | A: "A 32 bit checksum in the last 6 digits of each token … We start the implementation with a CRC32 algorithm … We then encode the result with a Base62 implementation, using leading zeros for padding as needed." |
| CRC32 computed over WHICH bytes (random part only vs prefix included) | **NOT FOUND** in A, B, C, D or E | None of these sources states the CRC32 input. I also found no GitHub-owned code that computes or validates it: cli/cli and octokit have prefix checks only (see Part 2). **Treat the CRC input as undocumented.** |
| Base62 alphabet order | **NOT FOUND** | A says only "a Base62 implementation". The digit/letter order (0-9A-Za-z vs A-Za-z0-9, etc.) is not stated anywhere official. |
| 6 chars, zero padding, left or right | PARTIAL | "last 6 digits" and "using leading zeros for padding" are CONFIRMED (A). "Leading" implies left-padding. Endianness and byte-order of the CRC before encoding are NOT FOUND. |
| Future length up to 255 | CONFIRMED | B and D: "integrators should plan to support tokens up to 255 characters after June 1, 2021." |
| **NEW (2026): ghs_ is no longer fixed-length** | CONFIRMED | C: "Starting April 27, 2026, GitHub began a staged rollout of a stateless format ( ghs_APPID_JWT ) to all newly minted GitHub App installation tokens … If your application expects or relies on installation tokens being exactly 40 characters long, it may not handle this new token format correctly." E: "A stateless token is a ghs_ -prefixed JWT . It is longer (~520 characters) and contains two dots"; "Our recommended regex to match both new and current format tokens is `ghs_[A-Za-z0-9\.\-_]{36,}`"; "Upcoming rollouts will apply the new token format only to GitHub App installation server-to-server tokens, including Actions GITHUB_TOKEN." E also says: "We'll share more details … on planned format changes for user-to-server tokens". So ghu_ may change as well. |

**Consequence for the corpus:** a generator can reproduce prefix + 30 base62 + 6 base62. It **cannot** reproduce a checksum that GitHub's own validator would accept, because the CRC input bytes and the Base62 alphabet order are not published by GitHub. Any exact algorithm would come from a non-provider source, which is excluded here. The ghs_ generator also needs the 2026 JWT variant (~520 chars, two dots).

### 2. GitLab legacy glpat-

Sources:
- https://docs.gitlab.com/security/tokens/ (token prefix table)
- GitLab source at gitlab-org/gitlab@18c8707f2614a1fe80779372380a0199d0d8d36d

| Fact | Status | Quote and source |
|---|---|---|
| Prefix `glpat-` | CONFIRMED | Docs table: "Personal access token glpat-" (also impersonation, project and group access tokens). Code: `app/models/personal_access_token.rb` line 22: `PERSONAL_TOKEN_PREFIX = 'glpat-'` — https://gitlab.com/gitlab-org/gitlab/-/blob/18c8707f2614a1fe80779372380a0199d0d8d36d/app/models/personal_access_token.rb#L22 |
| Legacy body = 20 chars | CONFIRMED (code + dependency) | `lib/authn/token_field/base.rb` line 149: `"#{prefix_for(token_owner_record)}#{Devise.friendly_token}"` — https://gitlab.com/gitlab-org/gitlab/-/blob/18c8707f2614a1fe80779372380a0199d0d8d36d/lib/authn/token_field/base.rb#L149 . Devise (heartcombo/devise@372b295, `lib/devise.rb` L511-515) defines `def self.friendly_token(length = 20)` … `SecureRandom.urlsafe_base64(rlength).tr('lIO0', 'sxyz')`. **Caveat:** Devise is a dependency, not a GitLab-owned repo. The docs line "The token must be 20 characters long" (docs.gitlab.com/user/profile/personal_access_tokens/, "Create a personal access token programmatically") covers tokens set manually through `set_token`, not generated ones. |
| Charset of the 20 | CONFIRMED via Devise code (dependency) | URL-safe base64 alphabet `[A-Za-z0-9_-]` with `l`, `I`, `O`, `0` replaced by `s`, `x`, `y`, `z`. The effective alphabet is 60 symbols: no `l`, `I`, `O` or `0`. Distribution is **non-uniform** because s/x/y/z are over-represented. GitLab's own docs do not state the charset. |
| **NEW: glpat- is no longer 20 chars for current tokens** | CONFIRMED (code) | `personal_access_token.rb` L28-36 declares `routable_token: { payload: { o: …organization_id, u: …user_id } }`. `lib/authn/token_field/generator/routable_token.rb` (same SHA) shows `RANDOM_BYTES_LENGTH = 16`, `CRC_BYTES = 7`, `Zlib.crc32(encoded).to_s(36).rjust(CRC_BYTES, '0')`, `Base64.urlsafe_encode64(encodable_payload, padding: false)`, and the format `"#{prefix}#{base64_payload}.#{token_version}.#{base64_payload_length}"` followed by the CRC. So a routable glpat- is: prefix, urlsafe-b64 of (16 random bytes + routing payload such as `o:<base36>\nu:<base36>` + 1 length byte), then `.`, a 2-char base36 version (`01`), `.`, a 2-char base36 payload length, then 7 chars of base36 CRC32. The CRC32 covers everything before it, **including the prefix**. Whether the routable path is on for a given instance depends on `generate_routable_token?` (feature condition). |

### 3. PyPI API tokens

Source: https://docs.pypi.org/api/secrets/ ("Detecting the PyPI secret format")

| Fact | Status | Quote |
|---|---|---|
| Prefix `pypi` + separator `-` | CONFIRMED | "A PyPI API token is a string consisting of a prefix ( pypi ), a separator ( - ) and a string representing a Macaroon base64 serialized with PyMacaroon" |
| Published pattern | CONFIRMED | `pypi-[A-Za-z0-9-_]{85,}` |
| Minimum length / unbounded | CONFIRMED | "The base64 string will not be shorter than 85 characters. A token can be arbitrarily long because we may add arbitrary caveats to the serialized Macaroon." |
| Macaroon structure | CONFIRMED in pypi/warehouse@8e4dc6d6 | `warehouse/macaroons/services.py`: L500-510 `pymacaroons.Macaroon(location=location, identifier=str(dm.id), key=dm.key, version=pymacaroons.MACAROON_V2)`, then `m.add_first_party_caveat(caveats.serialize(caveat))` for each scope, then L510 `serialized_macaroon = f"pypi-{m.serialize()}"`. `warehouse/db.py` L87: `id: Mapped[UUID]`, so the identifier is a 36-char UUID string. `warehouse/manage/views/__init__.py` L1053: `location=self.request.domain`. Parsing (services.py L40-41): `prefix, _, raw_macaroon = prefixed_macaroon.partition("-")`, `return None if prefix != "pypi" …`. Links: https://github.com/pypi/warehouse/blob/8e4dc6d6eb81c912b2070464166e8ece2be8b1f4/warehouse/macaroons/services.py#L500-L510 |

Generator implication: a realistic token is a V2 binary macaroon with location = domain, identifier = UUID string, first-party caveats (JSON-serialized by warehouse), and a 32-byte HMAC signature. It is urlsafe base64 without padding, which matches the published pattern.

### 4. Heroku HRKU-

| Fact | Status | Quote and source |
|---|---|---|
| Prefix `HRKU-` | CONFIRMED | https://devcenter.heroku.com/changelog-items/2800: "We now prefix all newly granted Heroku OAuth access tokens with HRKU- ." |
| 65 characters | CONFIRMED | https://devcenter.heroku.com/articles/oauth ("Prefixed OAuth Tokens"): "Heroku OAuth access tokens are 65 characters long and prefixed with HRKU- ." That gives 60 characters after the prefix. |
| Charset | **NOT FOUND** as a stated rule | No sentence states it. The official example in the oauth article is 65 chars total and uses only upper case, lower case, digits and `_`, which suggests base64url, but this is INFERRED from one example. |
| Conflicting example | Note | https://devcenter.heroku.com/changelog-items/2842 ("Starting April 1, 2024, we'll prefix …") gives a prefixed example in **UUID form**: `HRKU-` + 36-char hyphenated UUID = 41 chars. The API-response examples in the oauth article also use the UUID form (placeholders). The current authoritative statement is "65 characters". The UUID form likely reflects the pre-change announcement or placeholders, but the corpus may want both shapes. This is uncertain. |

### 5. Supabase sb_publishable_ / sb_secret_

| Fact | Status | Quote and source |
|---|---|---|
| Prefixes | CONFIRMED | https://supabase.com/docs/guides/api/api-keys: "Publishable key sb_publishable_... … Secret keys sb_secret_..."; also the announcement https://github.com/orgs/supabase/discussions/29260 |
| Total length, random length, checksum length and algorithm, charset | **NOT FOUND in docs** or in the announcement | The docs mention only "log no more than 6 characters from the random part after its prefix". |
| Structure from Supabase's own code | CONFIRMED (self-hosted generator) | supabase/supabase@ff80bb14 `docker/utils/add-new-auth-keys.sh` L125-135: `const random = crypto.randomBytes(17).toString("base64url").slice(0, 22);` `const intermediate = prefix + random;` `const checksum = crypto.createHash("sha256").update(PROJECT_REF + "\|" + intermediate).digest("base64url").slice(0, 8);` `return intermediate + "_" + checksum;` with `PROJECT_REF = "supabase-self-hosted"`. https://github.com/supabase/supabase/blob/ff80bb14991e68667c04f74248b954e8babe6fde/docker/utils/add-new-auth-keys.sh#L125-L135 |
| Derived lengths | INFERRED from that code | random = 22 base64url chars; checksum = 8 base64url chars (first 8 of base64url(SHA-256(project_ref + "\|" + prefix + random))); separator `_`. sb_publishable_ total = 15+22+1+8 = **46**; sb_secret_ total = 10+22+1+8 = **41**. Charset of both parts is `[A-Za-z0-9_-]`. The supabase/cli local defaults (`apps/cli-go/pkg/config/apikeys.go` L19-20 @b56c7659) have the same shape: 22 chars, `_`, 8 chars. **Caveat:** this is the self-hosted and CLI generator. Hosted-platform keys are not documented. They are probably the same shape, but that is unverified. |

### 6. Cloudflare cfk_ / cfut_ / cfat_ / cfast_

| Fact | Status | Quote and source |
|---|---|---|
| cfk_/cfut_/cfat_ = prefix + 40 + checksum | CONFIRMED | https://developers.cloudflare.com/fundamentals/api/get-started/token-formats/ (last updated Apr 20, 2026): "Each credential type has a distinct prefix followed by 40 characters and a checksum." Table: `cfk_[40 characters][checksum]`, `cfut_[40 characters][checksum]`, `cfat_[40 characters][checksum]`. |
| Charset of the 40 | **NOT FOUND** | Only "40 characters". The old-format table on the same page says "40-character alphanumeric string" (user/account tokens) and "37–45 character lowercase hex string" (Global API Key), but that describes the legacy format. |
| Checksum algorithm | CONFIRMED (CRC32) | https://developers.cloudflare.com/changelog/post/2026-04-14-cloudflare-api-token-detections/: "the new Cloudflare API credential format … uses a structured prefix and a CRC32 checksum suffix." |
| Checksum length and encoding (cfk/cfut/cfat) | **NOT FOUND** | Not stated on either page. |
| cfast_ | CONFIRMED | https://developers.cloudflare.com/changelog/post/2026-08-26-service-token-secret-format/: "Cloudflare Access service token Client Secrets created on or after August 26, 2026, use the format `cfast_[40 alphanumeric characters][8-character checksum]`." The checksum algorithm is NOT stated for cfast_. |
| Extra (Cloudflare-owned code) | Note | cloudflare/mcp@259b2afc `src/auth/api-token-mode.ts` L20-21: `if (token.startsWith('cfat_')) return 'account'` and `if (token.startsWith('cfut_') \|\| token.startsWith('cfoat_')) return 'user'`. This reveals a further prefix, **cfoat_**, which is undocumented (probably OAuth access tokens; that reading is unverified). cloudflare/Cloudflare-WordPress@3a16f405 `src/API/Client.php` L42-51 uses prefix checks only, plus the legacy-key check `strlen 37..45 && /^[0-9a-f]+$/`. |

### 7. Replicate r8_

| Fact | Status | Quote |
|---|---|---|
| 40 chars, starts with r8_ | CONFIRMED | https://replicate.com/docs/topics/security/api-tokens: "They are 40-character strings that always start with `r8_`". That leaves 37 after the prefix. |
| Charset | **NOT FOUND** | Not stated. Replicate code checks the prefix only: replicate/cog@6b32b31d `pkg/provider/replicate/replicate.go` L221 `if strings.HasPrefix(token, "r8_")`; replicate/image-editing-arena `components/ApiKeyModal.tsx` L75 `disabled={!key.startsWith('r8_')}`. |

### 8. Shopify shpat_ / shpca_ / shppa_ / shpss_ (live pages return 404; quoted from Wayback)

| Fact | Status | Quote and archive URL |
|---|---|---|
| shpat_/shpca_/shppa_: 32 → 38 chars | CONFIRMED | https://web.archive.org/web/20210123235212/https://shopify.dev/changelog/length-of-the-shopify-access-token-is-increasing: "Effective April 01, 2020 — The length of newly generated Shopify access tokens will increase from 32 to 38 characters, adding a static prefix of shpat_ (for public apps), shpca_ (for custom apps), or shppa_ (for legacy private apps)." |
| shpss_: 32 → 38 | CONFIRMED | https://web.archive.org/web/20210122224334/https://shopify.dev/changelog/app-secret-key-length-has-increased: "The length of all newly generated Shopify app secret keys has increased from 32 to 38, adding a static prefix of shpss_ , to make the secret keys easier to identify." |
| Charset of the 32 | NOT FOUND in the changelogs. Stated in a Shopify-owned code comment. | Shopify/cli@6e186a3e `packages/app/src/cli/services/app-security-engine/rules/secret-rules.ts` L25: "shpat_/shpca_/shppa_/shpss_ bodies are hex; shprt_/shpsb_/shptka_/shpua_ are alphanumeric." L31: `/shp(?:(?:at\|ca\|pa\|ss)_[a-fA-F0-9]{16,}\|(?:rt\|sb\|tka\|ua)_[a-zA-Z0-9]{16,})/`. **Caveat:** the same comment says these prefixes are "already public via shopify.dev docs and published secret-scanning rules (gitleaks, GitHub partner patterns)". The rule may therefore be partly derived from scanner rules. Treat "hex" as provider-asserted but weakly sourced. |

### 9. SendGrid

| Fact | Status | Quote and source |
|---|---|---|
| Always 69 chars | CONFIRMED | https://support.sendgrid.com/hc/en-us/articles/44146758703387-Can-I-Use-a-Reduced-Shorter-API-Key-Size-in-SendGrid: "SendGrid API keys are always 69 characters long. This length is fixed for all API keys generated by SendGrid". The Twilio-hosted copy (support.twilio.com …/47305226780059) returned HTTP 403 to curl and was not read. twilio.com/docs/sendgrid/api-reference/api-keys/create-api-keys: "API keys limited to 69 characters". |
| SG. prefix | CONFIRMED (code) | sendgrid/sendgrid-nodejs@498e2329 `packages/client/src/classes/client.js` L14 `const API_KEY_PREFIX = 'SG.';` and L64-65 `isValidApiKey(apiKey) { return this.isString(apiKey) && apiKey.trim().startsWith(API_KEY_PREFIX); }` (warn only, L49-50). |
| Two-part dot structure | CONFIRMED only as a placeholder shape | Create-API-keys response example: `"api_key" : "SG.xxxxxxxx.yyyyyyyy"`. This shows `SG.<part1>.<part2>`. **No official source states the part lengths (22/43) or the charset.** |

### 10. Sentry sntryu_ / sntrys_ (getsentry/sentry@88dcaf3f71fa6e10826027b307b722a770df82c6)

- `src/sentry/types/token.py` L10-13: `USER = "sntryu_"`, `ORG = "sntrys_"`, `USER_APP = "sntrya_"`, `INTEGRATION = "sntryi_"`. Link: https://github.com/getsentry/sentry/blob/88dcaf3f71fa6e10826027b307b722a770df82c6/src/sentry/types/token.py#L10-L13
- `src/sentry/models/apitoken.py` L106-112: `def generate_token(token_type …): if token_type: return f"{token_type}{secrets.token_hex(nbytes=32)}"`. L152-153: `if token_type == AuthTokenType.USER: plaintext_token = generate_token(token_type=AuthTokenType.USER)`. **sntryu_ = 7 + 64 lowercase hex = 71 chars (CONFIRMED).** Other API tokens and refresh tokens without a type are 64 hex with no prefix. https://github.com/getsentry/sentry/blob/88dcaf3f71fa6e10826027b307b722a770df82c6/src/sentry/models/apitoken.py#L106-L112
- `src/sentry/utils/security/orgauthtoken_token.py` L9, L23-34: `SENTRY_ORG_AUTH_TOKEN_PREFIX = "sntrys_"`; payload `{"iat": <float timestamp>, "url": <system.url-prefix>, "region_url": …, "org": <slug>}`; `secret = b64encode(secrets.token_bytes(nbytes=32)).decode("ascii").rstrip("=")`; `payload_encoded = base64_encode_str(json_str)` (standard b64encode, **padding kept**); `return f"{SENTRY_ORG_AUTH_TOKEN_PREFIX}{payload_encoded}_{secret}"`. The parser (L38) requires `token.count("_") == 2`. **sntrys_ = prefix + standard-base64(JSON) (may contain `+ / =`) + `_` + 43 chars of standard base64 (`[A-Za-z0-9+/]`). Length is variable (CONFIRMED).** https://github.com/getsentry/sentry/blob/88dcaf3f71fa6e10826027b307b722a770df82c6/src/sentry/utils/security/orgauthtoken_token.py#L17-L34

---

## PART 2: Official SDK format-validation code

Repos were cloned at HEAD on 2026-09-30. The SHAs are given so the line references stay valid.

**Stripe**: prefix checks only; no lengths.
- stripe/stripe-cli@19bfd1ab `pkg/validators/validate.go` `func APIKey`: `len(input) < 12` → "must be at least 12 characters long"; `keyParts := strings.Split(input, "_")`; `len(keyParts) < 3` → legacy key; `keyParts[0] != "sk" && keyParts[0] != "rk" && keyParts[0] != "rkcs"`. This implies the `<type>_<mode>_<body>` shape, min 12 chars, and one more prefix, **rkcs**.
- stripe/stripe-cli `pkg/reporting/scrub.go` L14/L18 (redaction): `\b((?:sk|rk|pk)_(?:live|test)_)[a-zA-Z0-9_]+\b` and `\bwhsec_[a-zA-Z0-9+/]+=*`. This implies body charset `[A-Za-z0-9_]`, and a whsec_ body in standard base64 with optional `=` padding. These are redaction heuristics, not a spec.
- stripe/stripe-android@4b110617 `stripe-core/.../ApiKeyValidator.kt` L15/L21: `require(!apiKey.startsWith("sk_"))`, `require(!apiKey.startsWith("rk_"))`. stripe/stripe-ios `String+StripeCore.swift` L21 `hasPrefix("sk_")`, L29 `hasPrefix("pk_")`; `STPAPIClient.swift` L85 `hasPrefix("uk_")` (user key), L182 `hasPrefix("pk_test")`.
- stripe-python@b48375a3 `stripe/_webhook.py` L155 and stripe-node@d1a1df11 `src/Webhooks.ts` L264: message "It should start with `whsec_`". Prefix hint only.

**Slack**: prefix checks only.
- slackapi/bolt-python@eddc4766 `slack_bolt/middleware/authorization/internals.py` L62: `return token is not None and token.startswith("xoxb-")`.
- slackapi/slack-cli `internal/goutils/strings.go` L124-131 (log redaction): `xapp-[\w.-]*`, `xoxb-[\w.-]*`, `xoxp-[\w.-]*`, `xoxe-[\w.-]*`. This implies a charset within `[A-Za-z0-9_.-]`, with no length.
- slackapi/java-slack-sdk `ToolingToken.java` L16-17 comments: refresh `xoxe-...`, access `xoxe.xoxp-...`. This confirms the rotated-token shape `xoxe.xoxp-`.
- python-slack-sdk@1fe0b8e7, node-slack-sdk@4dfe5eb4, bolt-js@b64c469e: docstrings only; no validation code found.

**Anthropic**: no validation code found. Searched anthropic-sdk-python@a7285e91 and anthropic-sdk-typescript@bf205868 for `sk-ant`, `startswith`/`startsWith`, length and regex checks on the key. The only hit is a doc comment: `src/resources/beta/environments/work.ts` L550 "Format: `sk-ant-req-...`". This is a further prefix for a session bearer credential.

**OpenAI**: no validation code for sk-, sk-proj- or sk-admin-. Searched openai-python@7f203fd5 and openai-node@8b5e1767. The only related code is openai-node `src/realtime/websocket.ts` L194 (and `src/beta/realtime/websocket.ts` L200): `apiKey?.startsWith('ek_')`. This is an ephemeral client-secret prefix check.

**Hugging Face**: prefix checks only.
- huggingface.js@e016dde7 `packages/hub/src/utils/checkCredentials.ts` L4-5: `if (!accessToken.startsWith("hf_")) { throw new TypeError("Your access token must start with 'hf_'"); }`.
- huggingface_hub@013afa78 `src/huggingface_hub/_login.py` L426: `if token.startswith("api_org"):`. This is the legacy org-token prefix. `inference/_client.py` L211 and `inference/_providers/_common.py` L221/L308 use `startswith("hf_")` to tell HF tokens apart from provider keys. No length or charset checks.

**DigitalOcean**: prefix list only.
- digitalocean/doctl@048b0c57 `commands/agents_validate.go` L88-103 `credentialPrefixes`: includes `"dop_v1_"`, `"doo_v1_"`. `dor_v1_` is not listed. There are no length checks.
- godo@dd7ac19e: comment only (`agent_inference.go` L17 "NOT a dop_v1_* token"). pydo@3a527de2: nothing.

**npm**: redaction regex only; no checksum code.
- npm/redact@df9d4daa (`@npmcli/redact`, vendored into npm/cli@0c3b82a9) `lib/matchers.js` L7: `pattern: /\b(npms?_)[a-zA-Z0-9]{36,48}\b/gi`. This implies prefixes `npm_` and `npms_`, and a body of 36-48 alphanumerics. It is a redaction range, not a spec.
- npm/cli and npm-registry-fetch@6b4159a2: no validation or CRC code found.
- Official statement on the checksum (not SDK): https://github.blog/changelog/2021-09-23-npm-has-a-new-access-token-format/ says "The last six characters of the tokens consist of CRC32 checksum, which is encoded in Base62". https://github.blog/security/announcing-npms-new-access-token-format/ says "By matching GitHub's token format … we increased our entropy from 128 to 178", which implies 30 base62 random chars (INFERRED). The CRC input and alphabet are not stated, as with GitHub.

**Docker**: no validation code found. Searched docker/cli@7fc2dff9 and docker/hub-tool@74bc560c for `dckr_`, `dckr_pat_`, `dckr_oat_` and token regex/length checks.

**Vercel**: one charset check.
- vercel/vercel@c628be78 `packages/cli/src/index.ts` L748: `const invalid = token.match(/(\W)/g);` → error "Must not contain …". This implies any `--token` must be `[A-Za-z0-9_]` only. `vcp_`/`vci_`/`vca_`/`vcr_`/`vck_` appear only in a help string (`commands/tokens/add.ts` L39 mentions "vcp_"). There is no prefix or length validation.
- Warning: `packages/cli/src/util/env/secret-detection.ts` L124-155 is a scanner-style list of other providers' patterns (GitHub, Stripe, SendGrid `SG\.[..]{22}\.[..]{43}`, etc.). It is excluded by the source rule and must not be cited for those providers.
- @vercel/sdk was not searched separately (separate repo `vercel/sdk`); status unknown.

**Netlify**: no validation code found. Searched netlify/cli@f845e14c, netlify/js-client@c451156e and netlify/build@227492a1 (which houses the current `@netlify/api`) for `nfp_`, `nfc_`, `nfo_`, `nfu_`, `nfb_` and token regexes. The only hit was a false positive (`nfo_` inside `info_`).

**Notion**: no validation code found. Searched makenotion/notion-sdk-js@25a755e7 for `ntn_` and `secret_`. `notion-sdk-py` is **not** in the makenotion org (clone of makenotion/notion-sdk-py failed: repo not found). The widely used Python SDK is community-owned and was excluded.

**GitHub**: prefix checks only; no github_pat_ length and no checksum code.
- cli/cli@fc4b137c `internal/gh/gh.go` L113-121: `TokenTypeOAuth = "gho_"`, `TokenTypePersonalAccess = "ghp_"`, `TokenTypeFineGrainedPAT = "github_pat_"`, `TokenTypeUserToServer = "ghu_"`, `TokenTypeServerToServer = "ghs_"`, `TokenTypeRefresh = "ghr_"`. `pkg/cmd/auth/status/status.go` L338: `knownTokenPrefixes = []string{"github_pat_", "ghp_", "gho_", "ghu_", "ghs_", "ghr_"}`.
- octokit/auth-token.js `src/auth.ts`: `token.startsWith("v1.") \|\| token.startsWith("ghs_")` → installation, `startsWith("ghu_")` → user-to-server. `src/is-jwt.ts`: JWT regex `^[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+$`.
- No ghp_ checksum validation exists in any GitHub-owned repo searched (cli/cli, octokit org).

**Google**: no AIza validation code found. Searched google-api-python-client@496433cb, python-genai@94b371d7 and js-genai@6c073f14. The only key-shape check is python-genai `google/genai/_api_client.py` L1452 and `live.py` L976: `api_key.startswith('auth_tokens/')`, which identifies ephemeral tokens.

**AWS**: no runtime validation; the service models carry constraints.
- botocore@21f2f87d `botocore/data/iam/2010-05-08/service-2.json`: `accessKeyIdType {'type':'string','max':128,'min':16,'pattern':'[\\w]+'}`; `accessKeySecretType {'type':'string','sensitive':True}` (no length). Also `sts/2011-06-15/service-2.json`: same with pattern `[\\w]*`. aws-sdk-js-v3 `codegen/sdk-codegen/aws-models/sts.json`: `accessKeyIdType` length min 16 / max 128, pattern `^[\w]*$`. **The SDKs do not fix the access key ID at 20 or the secret at 40.**
- aws-sdk-js-v3 `clients/client-sts/src/commands/GetAccessKeyInfoCommand.ts` (doc comment): "Access key IDs beginning with AKIA are long-term credentials … beginning with ASIA are temporary credentials". Its examples are [official example: 20 chars, AKIA prefix] and [official example secret: 40 chars, alphanumerics plus `/`].
- IAM docs https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_identifiers.html ("Understanding unique ID prefixes"): "AKIA Access key", "ASIA Temporary (AWS STS) access key IDs", "ABIA AWS STS service bearer token", "ACCA Context-specific credential".
- botocore and aws-cli@a821d3a9 have no AKIA/length checks in credential code.

**Datadog**: the SDKs have no validation. The agent's log scrubber encodes the shapes.
- datadog-api-client-python@b6651123 and datadog-api-client-go@13a33e98: no key validation found.
- DataDog/datadog-agent@26b389bc `pkg/util/scrubber/default.go`: L50 comment "mask the value regardless if it doesn't match 32-char hexadecimal string" (API key); L58 "40-char hexadecimal string" (app key); L100-101 `\b[a-fA-F0-9]{28}([a-fA-F0-9]{4})\b` (32-hex API key); L113-114 `\b[a-fA-F0-9]{36}([a-fA-F0-9]{4})\b` (40-hex app key); L65-66 `ddapp_[a-zA-Z0-9_]{30}([a-zA-Z0-9_]{4})` (new prefixed app key: `ddapp_` + 34 `[A-Za-z0-9_]`, added in agent 7.78.5); L119-120 `\bDDRCM_[A-Z0-9]+([A-Z0-9]{5})\b` (remote-config key). These are redaction patterns in provider-owned code, not validators.

---

## Summary of gaps (things no provider-owned source states)
- GitHub and npm: CRC32 input bytes, Base62 alphabet order and CRC byte order. Only "CRC32, Base62, last 6, leading-zero padding" is official.
- GitHub: the 2026 ghs_ JWT format (~520 chars, two dots) breaks the fixed-40 assumption; ghu_ changes are announced as coming.
- GitLab: current glpat- tokens are routable (variable length, base36 CRC32 over prefix+payload), not prefix+20.
- Heroku: charset not stated; UUID-form examples conflict with "65 characters".
- Supabase: lengths come only from the self-hosted/CLI generator; hosted-platform format is undocumented.
- Cloudflare: charset of the 40, checksum length and encoding (cfk/cfut/cfat), and cfast_ checksum algorithm are not stated. There is an undocumented `cfoat_` prefix.
- Replicate: charset not stated.
- Shopify: hex body is asserted only in a Shopify CLI comment that also cites scanner rules.
- SendGrid: part lengths and charset are not stated; only "69", "SG." and the placeholder `SG.xxxxxxxx.yyyyyyyy`.
- Provider-owned code revealed extra prefixes: Stripe `rkcs`/`uk_`, Anthropic `sk-ant-req-`, OpenAI `ek_`, npm `npms_`, Datadog `ddapp_`/`DDRCM_`, Cloudflare `cfoat_`, Sentry `sntrya_`/`sntryi_`.
