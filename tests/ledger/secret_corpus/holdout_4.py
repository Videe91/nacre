"""
Sealed holdout H4 for the secret-detection corpus. Test code only; never imported by src/.

Sealed holdout H4, sealed 2026-10-01 by a separate session that did not read detector code
(strip_secrets.py, its rule data, the staged-secrets scanner, the detector tests, the measurement
script or the measurement evidence). New seed (from os.urandom) and new embedding contexts, chosen
independently of the detector and disjoint from every context used in the working set, H1, H2 and H3.
Built to measure the credential-slot target (catch >= 90%, FP <= 2% on H4's negatives).
Never consult H4 samples or results while writing or tuning rules.

Contexts (each embeds the value exactly once, unmodified; each works for multi-line documents too).
[SLOT] = the surrounding text names a credential at the value's position (HOLDOUT4_CREDENTIAL_SLOTS).
  0. [SLOT]    Java .properties: spring.datasource.password=value, unquoted, then another key.
  1. [SLOT]    systemd unit: Environment="EXPORTER_API_SECRET=value" (quote right after the value).
  2. [SLOT]    Python requests headers dict, single-quoted 'Authorization': 'Token value'.
  3. [SLOT]    .NET appsettings.json ADO.NET connection string, Password=value; then Encrypt=True.
  4. [SLOT]    Shell transcript: docker login --username ci-bot --password value registry.example.test.
  5. [SLOT]    Elixir config keyword private_key: as a triple-double-quote heredoc (multi-line value syntax).
  6. [SLOT]    Go raw-string constant: const signingSecret = `value` (backtick quoting).
  7. [SLOT]    .netrc entry: password value as the last bytes, no trailing newline.
  8. [SLOT]    .npmrc: //registry.npmjs.org/:_authToken=value, then always-auth=true.
  9. [no slot] psql aligned query output: value in the last column of a result row.
 10. [no slot] LaTeX paragraph: value inside \\texttt{...}.
 11. [no slot] NASM data section: msg db "value", 0.
 12. [no slot] Python doctest: the expected output line is 'value'.
 13. [no slot] Dart one-liner main => print('value'); as the last bytes, no trailing newline.
 14. [no slot] XML element with the value in a CDATA section.
 15. [no slot] GraphQL query: node(id: "value") with a selection set.
 16. [no slot] Shell transcript: printf '%s' 'value' | pbcopy.
"""
from secret_corpus import credential_slot, generic, providers, synthetic_negatives  # noqa: F401
from secret_corpus.corpus import build

HOLDOUT4_SEED = 4_074_191_310

HOLDOUT4_CONTEXTS = [
    lambda v: (
        "# application-prod.properties\n"
        "spring.datasource.url=jdbc:postgresql://db.internal.test:5432/app\n"
        "spring.datasource.username=app\n"
        f"spring.datasource.password={v}\n"
        "spring.jpa.open-in-view=false\n"
    ),
    lambda v: (
        "[Unit]\n"
        "Description=Report exporter\n"
        "\n"
        "[Service]\n"
        "ExecStart=/usr/local/bin/exporter --listen :9100\n"
        f'Environment="EXPORTER_API_SECRET={v}"\n'
        "Restart=on-failure\n"
    ),
    lambda v: (
        "import requests\n"
        "\n"
        "headers = {'Accept': 'application/json',\n"
        f"           'Authorization': 'Token {v}'}}\n"
        "resp = requests.get(URL, headers=headers, timeout=10)\n"
    ),
    lambda v: (
        "{\n"
        '  "ConnectionStrings": {\n'
        '    "Primary": "Server=sql.internal.test,1433;Database=orders;User Id=svc_orders;'
        f'Password={v};Encrypt=True"\n'
        "  },\n"
        '  "Logging": { "LogLevel": { "Default": "Warning" } }\n'
        "}\n"
    ),
    lambda v: (
        f"$ docker login --username ci-bot --password {v} registry.example.test\n"
        "WARNING! Using --password via the CLI is insecure. Use --password-stdin.\n"
        "Login Succeeded\n"
    ),
    lambda v: (
        "import Config\n"
        "\n"
        "config :billing, Billing.Signer,\n"
        '  key_id: "k-2026",\n'
        '  private_key: """\n'
        f"  {v}\n"
        '  """\n'
    ),
    lambda v: (
        "package auth\n"
        "\n"
        "// signingSecret is used for webhook HMACs.\n"
        f"const signingSecret = `{v}`\n"
    ),
    lambda v: (
        "machine api.example.test\n"
        "  login deploy\n"
        f"  password {v}"
    ),
    lambda v: (
        "registry=https://registry.npmjs.org/\n"
        f"//registry.npmjs.org/:_authToken={v}\n"
        "always-auth=true\n"
    ),
    lambda v: (
        " id |  kind   | note\n"
        "----+---------+------\n"
        f"  7 | fixture | {v}\n"
        "(1 row)\n"
        "\n"
    ),
    lambda v: (
        "\\section{Results}\n"
        f"The sample identifier \\texttt{{{v}}} appears in Table~\\ref{{tab:runs}}.\n"
    ),
    lambda v: (
        "section .data\n"
        f'    msg db "{v}", 0\n'
        "    len equ $ - msg\n"
    ),
    lambda v: (
        ">>> from codec import encode\n"
        ">>> encode(b'payload')\n"
        f"'{v}'\n"
    ),
    lambda v: (
        "// bin/show.dart\n"
        f"void main() => print('{v}');"
    ),
    lambda v: (
        '<?xml version="1.0"?>\n'
        "<record>\n"
        f"  <note><![CDATA[{v}]]></note>\n"
        "</record>\n"
    ),
    lambda v: (
        "query Lookup {\n"
        f'  node(id: "{v}") {{\n'
        "    id\n"
        "    __typename\n"
        "  }\n"
        "}\n"
    ),
    lambda v: (
        "$ cd ~/scratch\n"
        f"$ printf '%s' '{v}' | pbcopy\n"
    ),
]

# Indices whose surrounding text names a credential at the value's position (D-0011 amendment 8).
HOLDOUT4_CREDENTIAL_SLOTS = frozenset({0, 1, 2, 3, 4, 5, 6, 7, 8})


def build_holdout4(per_generator: int = 50):
    """Sealed holdout H4: own seed, own contexts, documents embedded, every context covered,
    credential slots labelled, synthetic negatives never embedded."""
    return build(seed=HOLDOUT4_SEED, per_generator=per_generator, contexts=HOLDOUT4_CONTEXTS,
                 embed_documents=True, cover_all_contexts=True,
                 credential_slots=HOLDOUT4_CREDENTIAL_SLOTS, embed_negatives=False)
