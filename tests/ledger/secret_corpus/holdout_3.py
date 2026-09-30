"""
Sealed holdout H3 for the secret-detection corpus. Test code only; never imported by src/.

Sealed holdout H3, generated 2026-09-30 by a separate session that did not read detector code
(strip_secrets.py, its rule data, the staged-secrets scanner, the detector tests or the measurement
script). New seed (from os.urandom) and new embedding contexts, chosen independently of the detector.
Never consult H3 samples or results while writing or tuning rules.

Contexts (each embeds the value exactly once, unmodified; each works for multi-line documents too).
[SLOT] = the surrounding text names a credential at the value's position (HOLDOUT3_CREDENTIAL_SLOTS).
  0. [SLOT]    Dockerfile ENV instruction, SERVICE_TOKEN="value", then a CMD line.
  1. [SLOT]    Rust: `let secret = String::from("value");` inside fn main.
  2. [SLOT]    Jupyter notebook JSON code cell whose source line is password = 'value'.
  3. [SLOT]    Swift initializer argument apiKey: "value", closing paren directly after the quote.
  4. [SLOT]    PowerShell $env:AUTH_TOKEN = 'value' as the last bytes, no trailing newline.
  5. [SLOT]    Ansible group_vars YAML: db_password as a folded block scalar (>-).
  6. [SLOT]    nginx location block: proxy_set_header X-Auth-Token value; (semicolon right after).
  7. [no slot] Unified git diff hunk adding the value as a bare line of a text fixture.
  8. [no slot] Go table-driven test case {name: "case 3", in: "value", want: nil},
  9. [no slot] strace output of a sendto() syscall with the value as the quoted buffer.
 10. [no slot] WebSocket frame dump: JSON message field "d", value then "}" as the last bytes.
 11. [no slot] CSV with header id,user,note; value is the last field, no trailing newline.
 12. [no slot] reStructuredText docs: value inside an indented code-block directive.
 13. [no slot] Makefile recipe (tab-indented) passing the value as a positional arg, then >/dev/null.
 14. [no slot] Clojure (def blob "value") as the last bytes, no trailing newline.
 15. [no slot] HTML element with the value in a single-quoted data-payload attribute.
 16. [no slot] Protobuf text format: payload: "value" inside a nested message.
"""
from secret_corpus import credential_slot, generic, providers, synthetic_negatives  # noqa: F401
from secret_corpus.corpus import build

HOLDOUT3_SEED = 1_375_181_615

HOLDOUT3_CONTEXTS = [
    lambda v: (
        "FROM python:3.12-slim\n"
        "WORKDIR /srv\n"
        f'ENV SERVICE_TOKEN="{v}"\n'
        'CMD ["python", "-m", "worker"]\n'
    ),
    lambda v: (
        "fn main() {\n"
        f'    let secret = String::from("{v}");\n'
        "    run(&secret);\n"
        "}\n"
    ),
    lambda v: (
        '{\n "cell_type": "code",\n "execution_count": 3,\n "metadata": {},\n "outputs": [],\n'
        f' "source": ["password = \'{v}\'"]\n'
        "}\n"
    ),
    lambda v: (
        "let client = Client(\n"
        '    baseURL: URL(string: "https://api.example.test")!,\n'
        f'    apiKey: "{v}")\n'
        "client.start()\n"
    ),
    lambda v: (
        "PS C:\\work\\uploader> Set-Location .\\build\n"
        f"PS C:\\work\\uploader\\build> $env:AUTH_TOKEN = '{v}'"
    ),
    lambda v: (
        "---\n"
        "db_host: db.internal.test\n"
        "db_user: app\n"
        "db_password: >-\n"
        f"  {v}\n"
        "db_port: 5432\n"
    ),
    lambda v: (
        "location /api/ {\n"
        "    proxy_pass http://upstream_api;\n"
        f"    proxy_set_header X-Auth-Token {v};\n"
        "}\n"
    ),
    lambda v: (
        "diff --git a/fixtures/sample.txt b/fixtures/sample.txt\n"
        "--- a/fixtures/sample.txt\n"
        "+++ b/fixtures/sample.txt\n"
        "@@ -1,2 +1,3 @@\n"
        " header line\n"
        f"+{v}\n"
        " footer line\n"
    ),
    lambda v: (
        "var cases = []struct {\n\tname string\n\tin   string\n\twant error\n}{\n"
        f'\t{{name: "case 3", in: "{v}", want: nil}},\n'
        "}\n"
    ),
    lambda v: (
        'connect(5, {sa_family=AF_INET, sin_port=htons(443)}, 16) = 0\n'
        f'sendto(5, "{v}", 88, MSG_NOSIGNAL, NULL, 0) = 88\n'
        "recvfrom(5, 0x7ffd2c10, 4096, 0, NULL, NULL) = -1 EAGAIN\n"
    ),
    lambda v: (
        "<< {\"op\":10,\"d\":{\"interval\":41250}}\n"
        f">> {{\"op\":2,\"d\":\"{v}\"}}"
    ),
    lambda v: (
        "id,user,note\n"
        "41,bob,none\n"
        f"42,alice,{v}"
    ),
    lambda v: (
        "Example output\n"
        "--------------\n"
        "\n"
        ".. code-block:: text\n"
        "\n"
        f"   {v}\n"
        "\n"
        "The value above is printed once at startup.\n"
    ),
    lambda v: (
        ".PHONY: deploy\n"
        "deploy:\n"
        f"\t./scripts/deploy.sh staging {v} >/dev/null\n"
    ),
    lambda v: (
        "(ns app.config)\n"
        "\n"
        f'(def blob "{v}")'
    ),
    lambda v: (
        '<section id="status">\n'
        f"  <div class=\"panel\" data-payload='{v}'></div>\n"
        "</section>\n"
    ),
    lambda v: (
        "entries {\n"
        '  label: "prod"\n'
        "  body {\n"
        f'    payload: "{v}"\n'
        "  }\n"
        "}\n"
    ),
]

# Indices whose surrounding text names a credential at the value's position (D-0011 amendment 8).
HOLDOUT3_CREDENTIAL_SLOTS = frozenset({0, 1, 2, 3, 4, 5, 6})


def build_holdout3(per_generator: int = 50):
    """Sealed holdout H3: own seed, own contexts, documents embedded, every context covered,
    credential slots labelled, synthetic negatives never embedded."""
    return build(seed=HOLDOUT3_SEED, per_generator=per_generator, contexts=HOLDOUT3_CONTEXTS,
                 embed_documents=True, cover_all_contexts=True,
                 credential_slots=HOLDOUT3_CREDENTIAL_SLOTS, embed_negatives=False)
