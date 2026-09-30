"""Tests for the TEMPORARY pre-commit scanner (D-0010). Delete with scripts/scan_staged_secrets.py
when #6 replaces it. Secret-shaped strings are built at runtime so none is ever committed."""
import importlib.util
import random
import string
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("scan_staged_secrets", ROOT / "scripts/scan_staged_secrets.py")
scanner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scanner)
GLOBAL_ALLOW, RULES = scanner.load()
rng = random.Random(20260930)


def _rand(chars, n):
    return "".join(rng.choice(chars) for _ in range(n))


def github_pat():
    return "gh" + "p_" + _rand(string.ascii_letters + string.digits, 36)


def aws_access_key_id():
    return "AK" + "IA" + _rand(string.ascii_uppercase + "234567", 16)


def _ids(path, text):
    return {rule_id for _, _, rule_id, _ in scanner.scan(path, text, GLOBAL_ALLOW, RULES)}


@pytest.mark.parametrize("make,rule", [(github_pat, "github-pat"), (aws_access_key_id, "aws-access-token")])
def test_known_formats_are_caught(make, rule):
    assert rule in _ids("app/config.py", f'token = "{make()}"\n')


def test_clean_code_passes():
    assert _ids("app/x.py", "def add(a, b):\n    return a + b\n") == set()


def test_allowlist_is_path_and_value_specific():
    line = 'TEST_ROLE_PASSWORD = "' + "nacre_test" + '_only"\n'   # built at runtime (D-0010)
    assert _ids("tests/conftest.py", line) == set()
    assert _ids("src/nacre/settings.py", line) != set()   # same value elsewhere is still flagged


def test_repo_tree_is_clean():
    files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    findings = []
    for name in files:
        data = (ROOT / name).read_bytes()
        if b"\0" not in data[:8192]:
            findings += scanner.scan(name, data.decode("utf-8", "replace"), GLOBAL_ALLOW, RULES)
    assert findings == []


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _run_hook_mode(repo):
    return subprocess.run([str(ROOT / ".venv/bin/python"), str(ROOT / "scripts/scan_staged_secrets.py")],
                          cwd=repo, capture_output=True, text=True).returncode


def test_staged_mode_blocks_a_staged_secret(tmp_path):
    _git(tmp_path, "init", "-q")
    (tmp_path / "app.py").write_text(f'key = "{github_pat()}"\n')
    _git(tmp_path, "add", "app.py")
    assert _run_hook_mode(tmp_path) == 1


def test_staged_mode_scans_the_index_not_the_working_tree(tmp_path):
    _git(tmp_path, "init", "-q")
    (tmp_path / "app.py").write_text("key = None\n")
    _git(tmp_path, "add", "app.py")
    (tmp_path / "app.py").write_text(f'key = "{github_pat()}"\n')   # unstaged edit
    assert _run_hook_mode(tmp_path) == 0
    _git(tmp_path, "add", "app.py")
    assert _run_hook_mode(tmp_path) == 1


def test_format_doc_allowlist_is_narrow():
    # Quoted PEM labels in the format doc pass, but a real-shaped PEM body there is still caught.
    import base64, secrets
    doc = "docs/assumptions/evidence/A-0010-provider-formats-full-unverified.md"
    label = "-----BEGIN " + "PRIVATE KEY-----"
    prose = f"| PKCS#8 | `{label}` | " + "x" * 80 + " | `-----END " + "PRIVATE KEY-----` |\n"
    assert _ids(doc, prose) == set()
    body = base64.b64encode(secrets.token_bytes(600)).decode()
    pem = label + "\n" + "\n".join(body[i:i + 64] for i in range(0, len(body), 64)) + "\n-----END " + "PRIVATE KEY-----\n"
    assert "private-key" in _ids(doc, pem)
