"""
Sealed holdout H6 for the secret-detection corpus. Test code only; never imported by src/.

Sealed holdout H6, sealed 2026-10-02 by a separate session that did not read detector code
(strip_secrets.py, its rule data, the staged-secrets scanner, the slot-regex builder, the hooks, the detector
tests, the measurement script or the measurement evidence) and ran no detector, hook or measurement. New seed
(from os.urandom) and new embedding contexts, written from each format's own documentation, disjoint from every
context used in the working set, H1, H2, H3, H4 and H5.
Built for the PRE-REGISTERED revised gate item 12 (A-0010 holdout log, 2026-10-02), measured ONCE:
credential-slot catch per character pooled over COMMON slot contexts >= 95%, pooled over LONG-TAIL slot contexts
>= 80%, false positives on H6 negatives <= 2%. Never consult H6 samples or results while writing or tuning rules.

Common / long-tail classification (HOLDOUT6_CONTEXT_CLASS), fixed BEFORE the corpus was built.
Rule, stated before classifying:
  (a) the context's credential placement is in an owner-listed format (.env, YAML, JSON, TOML, .properties,
      Dockerfile, Kubernetes manifests, CLI flags, connection strings) -> common;
  (b) otherwise it is common only if the syntax that places the credential belongs to a language/format used by
      >= 40% of all respondents in the Stack Overflow Developer Survey 2025 ("used" share, not admired/desired);
      the 40% bar sits below every owner-listed carrier with survey data (Docker 71.1%, and JSON/YAML/.env are
      universal) and above the next tier;
  (c) everything else -> long-tail. A positional argument or protocol command is placed by that tool's or
      protocol's own grammar, so it is judged by that tool's prevalence, not by the shell that carries it.
Sources (retrieved 2026-10-02):
  [SO25] Stack Overflow Developer Survey 2025, Technology: https://survey.stackoverflow.co/2025/technology/
         languages used, all respondents: Bash/Shell 48.7%, HTML/CSS 61.9%, C# 27.8%, PHP 18.9%, Lua 9.2%,
         Ruby 6.4%, Groovy 4.8%; cloud tools: Docker 71.1%, Kubernetes 28.5%, Terraform 17.8%; databases:
         MongoDB 24%, Redis 28%. RabbitMQ, Apache httpd and IMAP clients are not listed.
  [OCT25] GitHub Octoverse 2025 (github.blog/news-insights/octoverse/): Shell ranks 7th and HCL 9th among
         languages by contributors; HCL fastest-growing (+56.1%). Growth is not prevalence of a credential
         placement; recorded, not used to promote HCL past rule (b) (Terraform 17.8% in [SO25]).
  [CNCF] CNCF Annual Survey (cncf.io/reports): Kubernetes production use among cloud-native orgs; supports the
         owner's Kubernetes listing, not needed for any non-owner-listed context.
Per context: owner-listed (a) for 0-11; 12 shell heredoc -> common by (b) (Bash/Shell 48.7%); 13-19 long-tail by
(c): Apache htpasswd (no survey data), RabbitMQ (not listed), IMAP protocol (no survey data), NuGet.config (C#
27.8%), Lua (9.2%), Terraform/HCL heredoc (Terraform 17.8%), Groovy (4.8%), Ruby (6.4%), PHP (18.9%).

Contexts (each embeds the value exactly once, unmodified; each works for multi-line documents too).
[SLOT] = the surrounding text names a credential at the value's position (HOLDOUT6_CREDENTIAL_SLOTS).
[C]/[L] = common / long-tail. Shapes: [CL] command line, [AP] attribute pair, [HD] heredoc / multi-line string.
  0. [SLOT][C]      .env.local with a double-quoted SESSION_SECRET between other keys.
  1. [SLOT][C]      docker-compose.yml service environment list item - POSTGRES_PASSWORD=value.
  2. [SLOT][C][AP]  JSON Postman environment export: {"key": "apiKey", "value": "value", "type": "secret"}.
  3. [SLOT][C]      TOML inline table auth = { user = "ci", password = "value" } in a [registries] table.
  4. [SLOT][C]      Java .properties with spaced colon separator: mail.smtp.password : value.
  5. [SLOT][C][HD]  Dockerfile BuildKit heredoc COPY <<-"EOT" /run/secrets/registry_token, value tab-indented.
  6. [SLOT][C][AP]  Kubernetes Deployment container env: - name: GRAFANA_ADMIN_PASSWORD / value: "value".
  7. [SLOT][C]      Kubernetes Secret stringData: webhook-hmac-key: value (plain scalar).
  8. [SLOT][C][CL]  CLI flag with a space and a scheme prefix: openssl pkcs12 -export ... -passout pass:value.
  9. [SLOT][C][CL]  CLI flag with = inside a quoted argument: terraform apply -var "db_password=value".
 10. [SLOT][C]      Connection string: mongodb+srv://ingest:value@cluster in a JavaScript client constructor.
 11. [SLOT][C]      ODBC connection string Uid=...;Pwd=value; passed to pyodbc.connect.
 12. [SLOT][C][HD]  Shell heredoc on stdin of kubectl create secret ... --from-file=signing-key=/dev/stdin <<'EOF'.
 13. [SLOT][L][CL]  htpasswd -bB file admin value (positional password after the username).
 14. [SLOT][L][CL]  rabbitmqctl add_user ingest value (positional password after the username), then set_permissions.
 15. [SLOT][L][CL]  IMAP protocol transcript: a001 LOGIN alice@example.test "value" (quoted), then OK.
 16. [SLOT][L][AP]  NuGet.config packageSourceCredentials: <add key="ClearTextPassword" value="value" />.
 17. [SLOT][L][AP]  Lua table list: { name = "smtp_password", value = "value" }, in a settings module.
 18. [SLOT][L][HD]  Terraform variable "db_password" default = <<-EOT heredoc, value indented, EOT.
 19. [SLOT][L][HD]  Jenkinsfile Groovy def registryPassword = ''' triple-single-quoted string, value indented.
 20. [SLOT][L][HD]  Ruby SIGNING_KEY = <<-'KEY'.strip heredoc, value indented, then KEY.
 21. [SLOT][L]      PHP wp-config.php define( 'DB_PASSWORD', 'value' ); after DB_USER.
 22. [no slot]      Cargo.lock: a package entry with a computed benign checksum, then value as the next checksum.
 23. [no slot]      Release JSON: version, a computed benign commit id, build_id: value, computed checksum.
 24. [no slot]      OCaml: let () = print_endline "value".
 25. [no slot]      Prolog fact: sample('value').
 26. [no slot]      CMake: message(STATUS "fixture: value").
 27. [no slot]      Markdown changelog bullet: - Fixed rendering of value in the preview pane.
 28. [no slot]      Octave/MATLAB script: disp('value').
 29. [no slot]      Zig: std.debug.print("{s}\\n", .{"value"}); inside pub fn main.
The benign values in 22 and 23 are derived with hashlib at import (no literal hash is written in this file).
"""
import hashlib

from secret_corpus import credential_slot, generic, providers, synthetic_negatives  # noqa: F401
from secret_corpus.corpus import build

HOLDOUT6_SEED = 2_441_296_908        # os.urandom, drawn once 2026-10-02 by the blind H6 session

_PREV_CHECKSUM = hashlib.sha256(b"h6 benign: crate tarball itoa").hexdigest()
_RELEASE_COMMIT = hashlib.sha1(b"h6 benign: release commit").hexdigest()
_RELEASE_SHA256 = hashlib.sha256(b"h6 benign: release artifact").hexdigest()

HOLDOUT6_CONTEXTS = [
    lambda v: (
        "# .env.local (not committed)\n"
        "NEXT_PUBLIC_SITE_URL=http://localhost:3000\n"
        f'SESSION_SECRET="{v}"\n'
        "LOG_LEVEL=debug\n"
    ),
    lambda v: (
        "services:\n"
        "  db:\n"
        "    image: postgres:16\n"
        "    environment:\n"
        "      - POSTGRES_USER=orders\n"
        f"      - POSTGRES_PASSWORD={v}\n"
        "    ports:\n"
        '      - "5432:5432"\n'
    ),
    lambda v: (
        "{\n"
        '  "name": "staging",\n'
        '  "values": [\n'
        '    {"key": "baseUrl", "value": "https://staging.example.test", "type": "default", "enabled": true},\n'
        f'    {{"key": "apiKey", "value": "{v}", "type": "secret", "enabled": true}}\n'
        "  ],\n"
        '  "_postman_variable_scope": "environment"\n'
        "}\n"
    ),
    lambda v: (
        "[registries.internal]\n"
        'index = "sparse+https://crates.example.test/index/"\n'
        f'auth = {{ user = "ci", password = "{v}" }}\n'
    ),
    lambda v: (
        "# mail.properties\n"
        "mail.smtp.host : smtp.example.test\n"
        "mail.smtp.user : notifier\n"
        f"mail.smtp.password : {v}\n"
        "mail.smtp.starttls.enable : true\n"
    ),
    lambda v: (
        "# syntax=docker/dockerfile:1\n"
        "FROM alpine:3.20\n"
        'COPY <<-"EOT" /run/secrets/registry_token\n'
        f"\t{v}\n"
        "EOT\n"
        'CMD ["/usr/local/bin/pull-images"]\n'
    ),
    lambda v: (
        "      containers:\n"
        "      - name: grafana\n"
        "        image: grafana/grafana:11.2.0\n"
        "        env:\n"
        "        - name: GRAFANA_ADMIN_USER\n"
        "          value: admin\n"
        "        - name: GRAFANA_ADMIN_PASSWORD\n"
        f'          value: "{v}"\n'
    ),
    lambda v: (
        "apiVersion: v1\n"
        "kind: Secret\n"
        "metadata:\n"
        "  name: webhook-config\n"
        "type: Opaque\n"
        "stringData:\n"
        f"  webhook-hmac-key: {v}\n"
    ),
    lambda v: (
        "$ openssl pkcs12 -export -in tls.crt -inkey tls.key \\\n"
        f"    -out bundle.p12 -passout pass:{v}\n"
        "$ ls -l bundle.p12\n"
    ),
    lambda v: (
        f'terraform apply -var "db_password={v}" -auto-approve\n'
    ),
    lambda v: (
        "import { MongoClient } from 'mongodb';\n"
        "\n"
        "const client = new MongoClient(\n"
        f"  'mongodb+srv://ingest:{v}@cluster0.example.test/events?retryWrites=true&w=majority'\n"
        ");\n"
    ),
    lambda v: (
        "import pyodbc\n"
        "\n"
        "conn = pyodbc.connect(\n"
        '    "Driver={ODBC Driver 18 for SQL Server};Server=tcp:sql.example.test,1433;"\n'
        f'    "Database=reports;Uid=report_reader;Pwd={v};Encrypt=yes;"\n'
        ")\n"
    ),
    lambda v: (
        "kubectl -n payments create secret generic webhook \\\n"
        "  --from-file=signing-key=/dev/stdin <<'EOF'\n"
        f"{v}\n"
        "EOF\n"
    ),
    lambda v: (
        "# create the dashboard login\n"
        f"sudo htpasswd -bB /etc/nginx/.htpasswd admin {v}\n"
        "sudo systemctl reload nginx\n"
    ),
    lambda v: (
        f"rabbitmqctl add_user ingest {v}\n"
        'rabbitmqctl set_permissions -p / ingest ".*" ".*" ".*"\n'
    ),
    lambda v: (
        "* OK [CAPABILITY IMAP4rev1 STARTTLS AUTH=PLAIN] mail.example.test ready\n"
        f'a001 LOGIN alice@example.test "{v}"\n'
        "a001 OK LOGIN completed\n"
    ),
    lambda v: (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        "<configuration>\n"
        "  <packageSourceCredentials>\n"
        "    <internal>\n"
        '      <add key="Username" value="ci-feed" />\n'
        f'      <add key="ClearTextPassword" value="{v}" />\n'
        "    </internal>\n"
        "  </packageSourceCredentials>\n"
        "</configuration>\n"
    ),
    lambda v: (
        "local M = {}\n"
        "\n"
        "M.settings = {\n"
        '  { name = "smtp_host", value = "smtp.example.test" },\n'
        f'  {{ name = "smtp_password", value = "{v}" }},\n'
        "}\n"
        "\n"
        "return M\n"
    ),
    lambda v: (
        'variable "db_password" {\n'
        "  type      = string\n"
        "  sensitive = true\n"
        "  default   = <<-EOT\n"
        f"    {v}\n"
        "  EOT\n"
        "}\n"
    ),
    lambda v: (
        "pipeline {\n"
        "  agent any\n"
        "  stages {\n"
        "    stage('push') {\n"
        "      steps {\n"
        "        script {\n"
        "          def registryPassword = '''\n"
        f"            {v}\n"
        "          '''.trim()\n"
        "        }\n"
        "      }\n"
        "    }\n"
        "  }\n"
        "}\n"
    ),
    lambda v: (
        "module Webhooks\n"
        "  SIGNING_KEY = <<-'KEY'.strip\n"
        f"    {v}\n"
        "  KEY\n"
        "end\n"
    ),
    lambda v: (
        "<?php\n"
        "define( 'DB_NAME', 'wordpress' );\n"
        "define( 'DB_USER', 'wp_admin' );\n"
        f"define( 'DB_PASSWORD', '{v}' );\n"
        "define( 'DB_HOST', 'localhost' );\n"
    ),
    lambda v: (
        "[[package]]\n"
        'name = "itoa"\n'
        'version = "1.0.11"\n'
        'source = "registry+https://github.com/rust-lang/crates.io-index"\n'
        f'checksum = "{_PREV_CHECKSUM}"\n'
        "\n"
        "[[package]]\n"
        'name = "ryu"\n'
        'version = "1.0.18"\n'
        'source = "registry+https://github.com/rust-lang/crates.io-index"\n'
        f'checksum = "{v}"\n'
    ),
    lambda v: (
        "{\n"
        '  "version": "3.8.1",\n'
        f'  "commit": "{_RELEASE_COMMIT}",\n'
        f'  "build_id": "{v}",\n'
        f'  "checksum": "sha256:{_RELEASE_SHA256}"\n'
        "}\n"
    ),
    lambda v: (
        f'let () = print_endline "{v}"\n'
    ),
    lambda v: (
        ":- initialization(main).\n"
        f"sample('{v}').\n"
        "main :- sample(X), write(X), nl.\n"
    ),
    lambda v: (
        "cmake_minimum_required(VERSION 3.20)\n"
        "project(viewer C)\n"
        f'message(STATUS "fixture: {v}")\n'
    ),
    lambda v: (
        "## 0.9.2\n"
        "\n"
        f"- Fixed rendering of {v} in the preview pane.\n"
        "- Faster startup on large workspaces.\n"
    ),
    lambda v: (
        "% show_fixture.m\n"
        f"disp('{v}')\n"
    ),
    lambda v: (
        'const std = @import("std");\n'
        "\n"
        "pub fn main() void {\n"
        f'    std.debug.print("{{s}}\\n", .{{"{v}"}});\n'
        "}\n"
    ),
]

# Indices whose surrounding text names a credential at the value's position (D-0011 amendment 8).
HOLDOUT6_CREDENTIAL_SLOTS = frozenset(range(22))
# Pre-registered gate item 12 classes, fixed before the build (rule and sources in the module docstring).
HOLDOUT6_CONTEXT_CLASS = {**{i: "common" for i in range(13)}, **{i: "long-tail" for i in range(13, 22)}}
# Owner-listed common format each common slot context is written in (rule (a)); 12 is common by rule (b).
HOLDOUT6_OWNER_FORMAT = {0: ".env", 1: "YAML", 2: "JSON", 3: "TOML", 4: ".properties", 5: "Dockerfile",
                         6: "Kubernetes manifest", 7: "Kubernetes manifest", 8: "CLI flag", 9: "CLI flag",
                         10: "connection string", 11: "connection string"}
# Slot shapes required by the pre-registration.
HOLDOUT6_COMMAND_LINE_SLOTS = frozenset({8, 9, 13, 14, 15})
HOLDOUT6_ATTRIBUTE_PAIR_SLOTS = frozenset({2, 6, 16, 17})
HOLDOUT6_HEREDOC_SLOTS = frozenset({5, 12, 18, 19, 20})
# Non-slot contexts carrying computed benign high-entropy values next to version/commit/checksum/id words.
HOLDOUT6_BENIGN_ENTROPY_NON_SLOTS = frozenset({22, 23})


def build_holdout6(per_generator: int = 50):
    """Sealed holdout H6: own seed, own contexts, documents embedded, every context covered,
    credential slots labelled, synthetic negatives never embedded."""
    return build(seed=HOLDOUT6_SEED, per_generator=per_generator, contexts=HOLDOUT6_CONTEXTS,
                 embed_documents=True, cover_all_contexts=True,
                 credential_slots=HOLDOUT6_CREDENTIAL_SLOTS, embed_negatives=False)
