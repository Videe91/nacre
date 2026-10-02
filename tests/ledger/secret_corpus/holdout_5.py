"""
Sealed holdout H5 for the secret-detection corpus. Test code only; never imported by src/.

Sealed holdout H5, sealed 2026-10-02 by a separate session that did not read detector code
(strip_secrets.py, its rule data, the staged-secrets scanner, the detector tests, the measurement
script or the measurement evidence) and ran no detector or measurement. New seed (from os.urandom) and
new embedding contexts, chosen independently of the detector from each language's own documentation,
and disjoint from every context used in the working set, H1, H2, H3 and H4.
Built to measure the credential-slot target (catch >= 90% AND FP <= 2% on H5's negatives, together,
measured once). Owner requirement: multi-line string-literal contexts in a credential slot, in a
varied set of languages and config formats, plus ordinary single-line slots and non-slot contexts.
Never consult H5 samples or results while writing or tuning rules.

Contexts (each embeds the value exactly once, unmodified; each works for multi-line documents too).
[SLOT] = the surrounding text names a credential at the value's position (HOLDOUT5_CREDENTIAL_SLOTS).
[ML]   = the value sits on its own line inside a multi-line string literal (HOLDOUT5_MULTILINE_SLOTS for slots).
  0. [SLOT][ML] Bash: read -r -d '' DEPLOY_TOKEN <<'EOF' (quoted heredoc), value at column 0, then EOF.
  1. [SLOT][ML] Python: OAUTH_CLIENT_SECRET = ''' (triple single quotes), value indented 4, '''.strip().
  2. [SLOT][ML] PowerShell single-quoted here-string $apiKey = @' ... '@, value at column 0.
  3. [SLOT][ML] Kotlin raw string val clientSecret = \"\"\" ... \"\"\".trimIndent() inside an object.
  4. [SLOT][ML] Swift multi-line string literal static let password = \"\"\" (closing delimiter indented).
  5. [SLOT][ML] TOML multi-line literal string password = ''' in an [smtp] table.
  6. [SLOT][ML] C++ raw string literal kAuthToken = R"tok( ... )tok"; (custom delimiter), value indented 2.
  7. [SLOT][ML] Perl indented heredoc my $db_password = <<~"END"; then chomp.
  8. [SLOT][ML] Nix indented string accessSecret = '' ... ''; in a NixOS module attrset.
  9. [SLOT][ML] PHP nowdoc $privateKey = <<<'KEY' with an indented closing marker (PHP >= 7.3).
 10. [SLOT][ML] YAML literal block scalar with indentation indicator client_secret: |2- (extra indent kept).
 11. [SLOT][ML] .NET web.config <add key="Webhook:SigningKey" with a multi-line value="..." attribute.
 12. [SLOT]     .pypirc [pypi] section: username = __token__, then password = value as the last line.
 13. [SLOT]     C# OpenID Connect options lambda: options.ClientSecret = "value";
 14. [SLOT]     redis-cli transcript: AUTH default value, then OK.
 15. [no slot]  Fortran program: print *, 'value'
 16. [no slot]  Erlang function returning {ok, <<"value">>}. (binary literal)
 17. [no slot]  R: ids <- c("a1", "value") then length(ids).
 18. [no slot]  Haskell main = putStrLn "value" as the last bytes, no trailing newline.
 19. [no slot]  Gherkin scenario step: When I look up record "value"
 20. [no slot][ML] Bash: cat > fixtures/sample.txt <<EOF (unquoted heredoc writing a fixture), value at column 0.
 21. [no slot][ML] Ruby squiggly heredoc EXPECTED_OUTPUT = <<~TEXT, value indented 2.
"""
from secret_corpus import credential_slot, generic, providers, synthetic_negatives  # noqa: F401
from secret_corpus.corpus import build

HOLDOUT5_SEED = 615_367_920          # os.urandom, drawn once 2026-10-02 by the blind H5 session

HOLDOUT5_CONTEXTS = [
    lambda v: (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "read -r -d '' DEPLOY_TOKEN <<'EOF' || true\n"
        f"{v}\n"
        "EOF\n"
        "export DEPLOY_TOKEN\n"
    ),
    lambda v: (
        "# settings/production.py\n"
        "OAUTH_CLIENT_ID = 'reports-web'\n"
        "OAUTH_CLIENT_SECRET = '''\n"
        f"    {v}\n"
        "'''.strip()\n"
    ),
    lambda v: (
        "$uri = 'https://api.example.test/v2/jobs'\n"
        "$apiKey = @'\n"
        f"{v}\n"
        "'@\n"
        "Invoke-RestMethod -Uri $uri -Headers @{ 'X-Api-Key' = $apiKey }\n"
    ),
    lambda v: (
        "package com.example.billing\n"
        "\n"
        "object Credentials {\n"
        '    val clientSecret = """\n'
        f"        {v}\n"
        '    """.trimIndent()\n'
        "}\n"
    ),
    lambda v: (
        "enum Keys {\n"
        '    static let password = """\n'
        f"        {v}\n"
        '        """\n'
        "}\n"
    ),
    lambda v: (
        "[smtp]\n"
        'host = "mail.internal.test"\n'
        "port = 587\n"
        "password = '''\n"
        f"{v}\n"
        "'''\n"
    ),
    lambda v: (
        "#include <string>\n"
        "\n"
        'static const std::string kAuthToken = R"tok(\n'
        f"  {v}\n"
        ')tok";\n'
    ),
    lambda v: (
        "use strict;\n"
        "use warnings;\n"
        'my $db_password = <<~"END";\n'
        f"    {v}\n"
        "    END\n"
        "chomp $db_password;\n"
    ),
    lambda v: (
        "{ config, ... }:\n"
        "{\n"
        "  services.backup.settings = {\n"
        "    accessSecret = ''\n"
        f"      {v}\n"
        "    '';\n"
        "  };\n"
        "}\n"
    ),
    lambda v: (
        "<?php\n"
        "\n"
        "$privateKey = <<<'KEY'\n"
        f"    {v}\n"
        "    KEY;\n"
    ),
    lambda v: (
        "oauth:\n"
        "  client_id: dashboard\n"
        "  client_secret: |2-\n"
        f"      {v}\n"
        "  redirect_uri: https://dash.example.test/cb\n"
    ),
    lambda v: (
        "<configuration>\n"
        "  <appSettings>\n"
        '    <add key="Webhook:SigningKey"\n'
        '         value="\n'
        f"           {v}\n"
        '         " />\n'
        "  </appSettings>\n"
        "</configuration>\n"
    ),
    lambda v: (
        "[distutils]\n"
        "index-servers = pypi\n"
        "\n"
        "[pypi]\n"
        "username = __token__\n"
        f"password = {v}\n"
    ),
    lambda v: (
        "services.AddAuthentication()\n"
        "    .AddOpenIdConnect(options =>\n"
        "    {\n"
        '        options.ClientId = "portal";\n'
        f'        options.ClientSecret = "{v}";\n'
        "    });\n"
    ),
    lambda v: (
        "$ redis-cli -h cache.internal.test\n"
        f"cache.internal.test:6379> AUTH default {v}\n"
        "OK\n"
    ),
    lambda v: (
        "program show\n"
        "  implicit none\n"
        f"  print *, '{v}'\n"
        "end program show\n"
    ),
    lambda v: (
        "-module(refs).\n"
        "-export([sample/0]).\n"
        "\n"
        f'sample() -> {{ok, <<"{v}">>}}.\n'
    ),
    lambda v: (
        f'ids <- c("a1", "{v}")\n'
        "length(ids)\n"
    ),
    lambda v: (
        "module Main where\n"
        "\n"
        f'main = putStrLn "{v}"'
    ),
    lambda v: (
        "Feature: Record lookup\n"
        "  Scenario: existing record\n"
        "    Given the archive is loaded\n"
        f'    When I look up record "{v}"\n'
        "    Then I see 1 result\n"
    ),
    lambda v: (
        "mkdir -p fixtures\n"
        "cat > fixtures/sample.txt <<EOF\n"
        f"{v}\n"
        "EOF\n"
    ),
    lambda v: (
        "EXPECTED_OUTPUT = <<~TEXT\n"
        f"  {v}\n"
        "TEXT\n"
    ),
]

# Indices whose surrounding text names a credential at the value's position (D-0011 amendment 8).
HOLDOUT5_CREDENTIAL_SLOTS = frozenset(range(15))
# Credential slots where the value is on its own line inside a multi-line string literal (owner requirement).
HOLDOUT5_MULTILINE_SLOTS = frozenset(range(12))
# Non-slot contexts that are also multi-line string literals (decoys: a literal alone is not a slot).
HOLDOUT5_MULTILINE_NON_SLOTS = frozenset({20, 21})


def build_holdout5(per_generator: int = 50):
    """Sealed holdout H5: own seed, own contexts, documents embedded, every context covered,
    credential slots labelled, synthetic negatives never embedded."""
    return build(seed=HOLDOUT5_SEED, per_generator=per_generator, contexts=HOLDOUT5_CONTEXTS,
                 embed_documents=True, cover_all_contexts=True,
                 credential_slots=HOLDOUT5_CREDENTIAL_SLOTS, embed_negatives=False)
