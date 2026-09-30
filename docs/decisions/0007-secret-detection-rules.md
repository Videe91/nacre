# D-0007: Secret detection — vendored gitleaks rules + entropy check

- **Status:** accepted (owner decided, 2026-09-30)
- **Tier:** D2 (dependency: vendored third-party data)
- **Date:** 2026-09-30
- **Relies on assumptions:** A-0010, A-0017
- **Related:** D-0002 (secrets stripped before encryption, inside `append_event`)

## Amendments after acceptance (owner-directed; additive, no accepted text changed)
1. **2026-09-30 — engine:** A-0017 was invalidated. The rules run under `google-re2`, never Python
   `re` (D-0009).
2. **2026-09-30 — corpus construction** (replaces the corpus bullet's "seeded from gitleaks' own
   per-rule true-positive examples"):
   - **Positives are generated at test time**, from seeded generators. The repo holds the
     generators plus a sha256 of their output, and never a secret-shaped string (GitHub push
     protection, CURRENT.md).
   - **Generators are written from each provider's documented token format** (prefix, length,
     charset, checksum where one exists), **independently of the gitleaks regexes**, so the corpus
     cannot inherit a rule's mistakes. Each generator cites its source document.
   - **Negatives are committed normally:** real code, UUIDs, git hashes, base64 blobs, minified
     files, drawing on the 1,200-file set used for A-0017. They contain no secrets and measure the
     ≤ 2% false-positive target.
   - The unknown-format set (entropy-only) follows the same generator rule.
3. **2026-09-30 — coverage and targets (owner-approved).**
   - **Covered providers** (one generator each, from public format docs; a provider without public
     docs is substituted, and the substitution is recorded):
     - AI: OpenAI, Anthropic, Google AI/Gemini, Hugging Face, Groq, Mistral, Replicate.
     - Cloud: AWS, GCP, Azure, Cloudflare, DigitalOcean.
     - Code and packages: GitHub (classic + fine-grained), GitLab, Bitbucket, npm, PyPI, Docker Hub.
     - Hosting and databases: Vercel, Netlify, Heroku, Supabase, Databricks.
     - Payments and commerce: Stripe, Shopify.
     - Messaging: Slack, Twilio, SendGrid, Discord.
     - Work tools and monitoring: Atlassian, Linear, Notion, Datadog, Sentry, Okta.
   - **Generic category**, with its own targets: private keys (RSA/EC/OpenSSH/PEM), DB connection
     strings with passwords, credentials in URLs, JWTs, `.env`-style secret assignments.
   - **Per-provider target:** catch rate is reported per provider, and **≥ 99% applies to each
     covered provider individually**, not to the average.
   - **Coverage metric:** covered rules / total rules, reported separately, with every unmeasured
     rule listed by name. Unmeasured rules are never counted as caught.
   - **Negatives:** permissively licensed sources only (MIT, BSD, Apache-2.0, PSF), with
     attribution. Every negative file is scanned with the rules first, and any real secret found
     is excluded.
4. **2026-09-30 — measurement rules (owner-approved).**
   - **No official format doc** (Groq, Mistral, OpenAI project/service-account keys, Azure Entra
     secrets, Atlassian/Bitbucket, Databricks, Twilio auth tokens, Discord bot tokens, Linear,
     Datadog application keys, Okta): reported as **"unmeasured by provider; covered only by generic
     detection"**. The generic layer's catch rate (entropy check + generic rules) is measured
     separately on realistic examples of these providers' tokens.
   - **Prefix documented, length not:** facts come from real tokens the owner holds, via a local
     measuring script (`scripts/measure_token_format.py`). The script prints only prefix confirmation,
     length and charset, and never the token. Real tokens never enter the repo, chat or agent context.
     - Each fact is an assumption (A-ID) with sample count and date; **one sample = provisional**.
     - **Before falling back** to lengths from official examples, check the provider's **official
       SDKs** for format-validation code (provider-owned source).
   - **Public-by-design values are neutral:** excluded from both catch and false-positive rates and
     reported separately. They are **not stripped**, and are tagged "public credential" in
     metadata. Look-alikes must be told apart:
     - Supabase `anon` vs `service_role`: same JWT shape, told apart by the `role` claim; only
       `service_role` is secret.
     - Stripe `pk_` (public) vs `sk_` / `rk_` (secret).
     - Modern Sentry DSNs (public key only) vs legacy DSNs carrying a secret part (secret).

## Context
D-0002 requires secrets to be stripped before any write. A-0010 sets the bar: at least 99% caught
on known formats, at most 2% false positives, with unknown formats tracked separately. The owner
chose "established rules" over home-grown patterns.

## Options considered
1. **Vendor gitleaks' rule set as data + our own entropy check.** gitleaks is widely used, MIT
   licensed, and has ~200 maintained rules in one TOML file that stdlib `tomllib` can read. No
   runtime dependency. The catch: the rules are written for Go's RE2 regex engine, not Python `re` (A-0017).
2. **`detect-secrets` library (Yelp, Apache-2.0).** Python-native plugins and a baseline workflow.
   It's a runtime dependency with its own release cadence, has fewer provider-specific rules, and
   is oriented to scanning repos, not individual payloads.
3. **TruffleHog rules.** Strong detectors, but AGPL-3.0, and many detectors verify secrets over
   the network, which is unacceptable on an intake path.
4. **Hand-written patterns.** Full control, but we would have to maintain every provider format ourselves.

## Decision
Option 1.
- Vendor **gitleaks v8.30.1** `config/gitleaks.toml`, unmodified, as
  `src/nacre/ledger/data/gitleaks-v8.30.1.toml`. The MIT license text goes beside it
  (`LICENSE-gitleaks-v8.30.1`), and a repo-root `THIRD_PARTY_NOTICES.md` records the source URL,
  version, commit and license. Upgrading means a new file with a new version in its name plus a
  re-run of the corpus, never an in-place edit.
- Add a **Shannon-entropy check** for high-entropy tokens that no rule matches. The threshold and
  minimum length are D1 tuning parameters, set from the corpus and recorded in the `strip_secrets`
  file header.
- On a match, replace the secret span with `[REDACTED:<rule_id>]` (or `[REDACTED:entropy]`) before
  encryption. The list of fired rule ids travels inside the encrypted body, never in plaintext.
- **Labeled corpus** under `tests/ledger/secret_corpus/`:
  - known-format positives, one or more per rule, seeded from gitleaks' own per-rule true-positive
    examples (MIT) and synthetic generated tokens, never real secrets;
  - negatives: real diffs, logs and code with no secrets;
  - a separate unknown-format set that only the entropy check can catch.

  A-0010 is measured on the known-format set; unknown formats are reported separately.

## Why this one
It is the owner's choice. It gets the largest maintained rule set with no runtime dependency and a
permissive license. Keeping the file pinned and unmodified makes upgrades an explicit, measured
event.

## Consequences
- gitleaks features beyond `regex` (keywords prefilter, `secretGroup`, allowlists, `entropy`
  per rule) must be honoured by our loader or explicitly listed as unsupported in the file header.
  Unsupported rules count against A-0010.
- The corpus is a frozen test asset: its sha256 is recorded, and changes are reviewed like code.

## How we'd know it was wrong
- A-0010 misses 99% / 2% on the corpus after tuning.
- A-0017 fails, i.e. rules don't behave the same under Python `re`.
- The redaction marker breaks downstream parsing of diffs or structured payloads.
