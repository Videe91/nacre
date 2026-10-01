"""Tests for the pre-commit scan (D-0010), now running strip_secrets. Secret-shaped strings are built
at runtime so none is ever committed."""
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
rng = random.Random(20260930)


def _rand(chars, n):
    return "".join(rng.choice(chars) for _ in range(n))


def github_pat():
    return "gh" + "p_" + _rand(string.ascii_letters + string.digits, 36)


def aws_access_key_id():
    return "AK" + "IA" + _rand(string.ascii_uppercase + "234567", 16)


def _ids(path, text):
    return {rule_id for _, _, rule_id, _ in scanner.scan(path, text)}


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
        if b"\0" not in data[:8192] and not scanner._reviewed_negative(name, data):   # as the hook does
            findings += scanner.scan(name, data.decode("utf-8", "replace"))
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


def test_committed_negatives_are_skipped_only_while_unchanged(tmp_path):
    import json
    manifest = json.loads((ROOT / "tests/ledger/secret_corpus/negatives/MANIFEST.json").read_text())
    entry = next(f for f in manifest["files"] if f["prescan_findings"])      # the reviewed false positive
    path = "tests/ledger/secret_corpus/negatives/" + entry["path"]
    data = (ROOT / path).read_bytes()
    assert scanner._reviewed_negative(path, data)                            # reviewed bytes: skipped
    assert not scanner._reviewed_negative(path, data + b"\n# edited\n")    # any change: scanned again
    assert not scanner._reviewed_negative("tests/ledger/secret_corpus/negatives/new.py", b"x = 1\n")


def test_frozen_fixture_reviews_are_exact_value_and_scoped(tmp_path):
    # Owner approval 2026-10-01: hashed exact-value entries, honoured only at their own path, rule and value.
    import json
    entries = json.loads((ROOT / "tests/regression/mnexa/SECRET_SCAN_REVIEWED.json").read_text())["entries"]
    e = next(x for x in entries if x["path"].endswith("tasks_023.json"))
    text = (ROOT / e["path"]).read_text()
    flagged = {f[3] for f in scanner.scan("elsewhere/tasks_023.json", text)}
    assert flagged                                               # the same values are still caught at another path
    assert scanner.scan(e["path"], text) == []                   # and reviewed at their own path
    assert scanner.scan(e["path"], text + '\n"id": "' + github_pat() + '"\n') != []   # new values still caught
    assert all(x["reason"].strip() for x in entries)


def test_a_reviewed_entry_outside_the_approved_scope_is_refused(monkeypatch, tmp_path):
    import json
    bad = tmp_path / "r.json"
    bad.write_text(json.dumps({"entries": [{"path": "src/nacre/x.py", "rule_id": "entropy", "value_sha256": "0" * 64,
                                            "reason": "no"}]}))
    monkeypatch.setattr(scanner, "ROOT", tmp_path)
    monkeypatch.setattr(scanner, "_FIXTURE_REVIEW_FILE", "r.json")
    with pytest.raises(ValueError, match="outside the approved scope"):
        scanner._fixture_reviews()


# Fail-closed secret-corpus folders (owner, 2026-10-01).
_C = "tests/ledger/secret_corpus/"


def test_unregistered_corpus_folder_is_refused():
    assert "not registered" in scanner.corpus_folder_error(_C + "holdout_5_negatives/a.py", b"x = 1\n")
    assert "not registered" in scanner.corpus_folder_error(_C + "__pycache__/x.pyc", b"\0\0")


def test_registered_folders_and_top_level_files_pass():
    assert scanner.corpus_folder_error(_C + "corpus.py", b"x = 1\n") is None                 # top-level file
    assert scanner.corpus_folder_error(_C + "fonts/LICENSE-DejaVu.txt", b"text\n") is None   # working data
    assert scanner.corpus_folder_error(_C + "negatives_holdout4/new.py", b"x\n") is None    # skip-registered
    assert scanner.corpus_folder_error("src/nacre/x.py", b"x\n") is None                    # outside the corpus


def test_sealed_binary_folder_accepts_only_manifest_bytes():
    import json
    case = json.loads((ROOT / _C / "I1_MANIFEST.json").read_text())["cases"][0]
    path = _C + "i1_images/" + case["case_id"] + ".png"
    data = (ROOT / path).read_bytes()
    assert scanner.corpus_folder_error(path, data) is None
    assert "sha256 differs" in scanner.corpus_folder_error(path, data + b"x")
    assert "unlisted" in scanner.corpus_folder_error(_C + "i1_images/extra.png", data)


def test_staged_mode_refuses_an_unregistered_corpus_folder_without_scanning(tmp_path):
    _git(tmp_path, "init", "-q")
    d = tmp_path / _C / "holdout_9_negatives"
    d.mkdir(parents=True)
    (d / "a.py").write_text(f'key = "{github_pat()}"\n')   # would be a finding if it were scanned
    _git(tmp_path, "add", "-A")
    r = subprocess.run([str(ROOT / ".venv/bin/python"), str(ROOT / "scripts/scan_staged_secrets.py")],
                       cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode == 1
    assert "REFUSED" in r.stderr and "SECRET?" not in r.stderr   # refused before any detector ran

