"""
Sealed holdout H2 for the secret-detection corpus. Test code only; never imported by src/.

Generated 2026-09-30 by a separate session that did not read the detector code (strip_secrets.py,
its rule data, the staged-secrets scanner, the detector tests or the measurement script). New seed and
new embedding contexts, chosen independently of the detector. Never consult H2 samples or results while
writing or tuning rules.

Contexts (each embeds the value exactly once, unmodified; each works for multi-line documents too):
  1. Java text block: value inside a triple-quoted literal, closing quotes directly after it.
  2. Raw HTTP POST with CRLF headers and a form body; value is the last bytes, no trailing newline.
  3. GitHub Actions style CI log: timestamped "Run" step echoing a script call with the value quoted.
  4. Python traceback ending in a ValueError whose message quotes the value.
  5. Issue / chat comment: value inline in prose, directly followed by a comma.
  6. Terraform HCL provider block with the value in a quoted attribute.
  7. Interactive shell prompt: value as a CLI argument immediately followed by "&&".
  8. C# object initializer with a verbatim @"..." string.
  9. PHP associative array passed to a constructor, single-quoted value.
 10. Lua long-bracket string [[...]] with no whitespace around the value.
 11. Markdown table row, value between pipes.
 12. HTTP GET request line with the value as a query parameter followed by "&page=2".
 13. pytest assertion failure output comparing the value with an empty string.
 14. Ruby %q(...) literal passed as a keyword argument, then a method chain.
 15. Kubernetes Secret manifest with the value under a YAML block scalar.
 16. React JSX prop using a template literal, value directly followed by a backtick.
"""
from secret_corpus.corpus import build

HOLDOUT2_SEED = 3_671_910_561

HOLDOUT2_CONTEXTS = [
    lambda v: (
        "public final class Settings {\n"
        "    static final String CREDENTIAL = \"\"\"\n"
        f"        {v}\"\"\";\n"
        "}\n"
    ),
    lambda v: (
        "POST /v2/sessions HTTP/1.1\r\n"
        "Host: api.internal.test\r\n"
        "Content-Type: application/x-www-form-urlencoded\r\n"
        "Accept: */*\r\n"
        "\r\n"
        f"grant=client&credential={v}"
    ),
    lambda v: (
        "2026-09-30T08:14:01.5520311Z ##[group]Run ./scripts/deploy.sh\n"
        f"2026-09-30T08:14:01.5521004Z ./scripts/deploy.sh --target staging \"{v}\"\n"
        "2026-09-30T08:14:01.5521390Z shell: /usr/bin/bash -e {0}\n"
        "2026-09-30T08:14:01.5521822Z ##[endgroup]\n"
    ),
    lambda v: (
        "Traceback (most recent call last):\n"
        '  File "/srv/app/main.py", line 42, in <module>\n'
        "    client = connect(settings.CREDENTIAL)\n"
        '  File "/srv/app/client.py", line 17, in connect\n'
        "    raise ValueError(f\"rejected credential {cred!r}\")\n"
        f"ValueError: rejected credential '{v}'\n"
    ),
    lambda v: (
        "@maria I tried again with the one from the vault, "
        f"{v}, and it still returns 403 on the upload endpoint. Any idea?\n"
    ),
    lambda v: (
        'provider "acme" {\n'
        '  region     = "eu-west-1"\n'
        f'  credential = "{v}"\n'
        "}\n"
    ),
    lambda v: (
        "dev@build-07:~/work/uploader$ ./uploader auth set "
        f"{v}&& ./uploader sync --dry-run\n"
        "auth: stored\nsync: 0 files changed\n"
    ),
    lambda v: (
        "var options = new ClientOptions\n"
        "{\n"
        '    Endpoint = new Uri("https://api.example.test/"),\n'
        f'    Credential = @"{v}",\n'
        "};\n"
    ),
    lambda v: (
        "<?php\n"
        "$client = new Client([\n"
        "    'base_uri' => 'https://api.example.test',\n"
        f"    'credential' => '{v}',\n"
        "]);\n"
    ),
    lambda v: (
        "local M = {}\n"
        f"M.credential = [[{v}]]\n"
        "return M\n"
    ),
    lambda v: (
        "| environment | credential | notes |\n"
        "|---|---|---|\n"
        f"| staging | {v} | rotated last sprint |\n"
    ),
    lambda v: (
        f"GET /v1/export?format=csv&credential={v}&page=2 HTTP/1.1\n"
        "Host: reports.example.test\n"
        "User-Agent: python-requests/2.32.3\n"
    ),
    lambda v: (
        "    def test_credential_is_cleared():\n"
        ">       assert settings.credential == ''\n"
        f"E       AssertionError: assert '{v}' == ''\n"
        "\n"
        "tests/test_settings.py:31: AssertionError\n"
    ),
    lambda v: (
        f"client = Api::Client.new(credential: %q({v})).tap(&:connect!)\n"
    ),
    lambda v: (
        "apiVersion: v1\n"
        "kind: Secret\n"
        "metadata:\n"
        "  name: uploader\n"
        "type: Opaque\n"
        "stringData:\n"
        "  credential: |\n"
        f"    {v}\n"
    ),
    lambda v: (
        "export function App() {\n"
        f"  return <ApiProvider credential={{`{v}`}}>\n"
        "    <Dashboard />\n"
        "  </ApiProvider>;\n"
        "}\n"
    ),
]


def build_holdout2(per_generator: int = 50):
    """Sealed holdout H2: own seed, own contexts, documents embedded, every context covered."""
    return build(seed=HOLDOUT2_SEED, per_generator=per_generator, contexts=HOLDOUT2_CONTEXTS,
                 embed_documents=True, cover_all_contexts=True)
