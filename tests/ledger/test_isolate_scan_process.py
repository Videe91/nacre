"""Tests for the isolated attachment-scan process (D-0006 amendment 3 conditions): ledger/isolate_scan_process.py, the
child launch in ledger/scan_binary_attachment.py, and the child's JSON contract (ledger/run_binary_scan_child.py).
The probe goes through the real scan_binary_attachment() launch (command, sandbox, environment, limits, stdin); only
the child script is swapped for a probe that reports what it can and cannot do, as the reason of an unscannable
verdict (the one channel the parent accepts)."""
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

import nacre.ledger.scan_binary_attachment as scan
from nacre.ledger.isolate_scan_process import MEMORY_EXIT_CODE
from nacre.ledger.scan_binary_attachment import Clean, SecretDetected, Unscannable, scan_binary_attachment

SRC = Path(scan.__file__).resolve().parents[2]
PROBE = """
import ctypes, json, os, resource, socket, struct, subprocess, sys
sys.path.insert(0, {src!r})
from nacre.ledger.isolate_scan_process import ScanLimits, isolate_scan_process
mechanisms = isolate_scan_process(ScanLimits(**json.loads(sys.argv[1])))
def attempt(action):
    try:
        action()
        return "allowed"
    except Exception as exc:
        return f"{{type(exc).__name__}}: {{exc}}"
def write_file():
    with open({target!r}, "wb") as f:
        f.write(b"x")
def os_connect():            # straight to libc, as native code would: only an OS sandbox can stop this
    libc = ctypes.CDLL(None, use_errno=True)
    fd = libc.socket(2, 1, 0)
    if fd < 0:
        return ctypes.get_errno()
    addr = struct.pack("BBH4s8x", 16, 2, socket.htons(9), bytes([127, 0, 0, 1]))
    return 0 if libc.connect(fd, addr, len(addr)) == 0 else ctypes.get_errno()
report = {{
    "env": dict(os.environ), "mechanisms": mechanisms,
    "core": resource.getrlimit(resource.RLIMIT_CORE), "fsize": resource.getrlimit(resource.RLIMIT_FSIZE),
    "nofile": resource.getrlimit(resource.RLIMIT_NOFILE), "cpu": resource.getrlimit(resource.RLIMIT_CPU),
    "connect": attempt(lambda: socket.create_connection(("127.0.0.1", 9), timeout=1)),
    "dns": attempt(lambda: socket.getaddrinfo("localhost", 80)), "os_connect_errno": os_connect(),
    "write": attempt(write_file), "spawn": attempt(lambda: subprocess.run(["/bin/echo"], capture_output=True)),
    "stdin": len(sys.stdin.buffer.read()),
}}
print(json.dumps({{"verdict": "unscannable", "reason": json.dumps(report)}}))
"""


def _probe(tmp_path, monkeypatch):
    target = tmp_path / "written-by-child"
    script = tmp_path / "probe.py"
    script.write_text(PROBE.format(src=str(SRC), target=str(target)))
    for name in ("PGPASSWORD", "PGHOST", "NACRE_DSN", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "HTTPS_PROXY"):
        monkeypatch.setenv(name, "must-not-reach-the-child")
    monkeypatch.setattr(scan, "_CHILD", script)
    verdict = scan_binary_attachment(b"x" * 1000)
    assert isinstance(verdict, Unscannable) and verdict.reason.startswith("{"), verdict
    return json.loads(verdict.reason), target


def test_the_child_gets_no_environment_no_network_no_writes_and_hard_limits(tmp_path, monkeypatch):
    report, target = _probe(tmp_path, monkeypatch)
    # The parent passes {}; the only variables are set inside the child by macOS (__CF_USER_TEXT_ENCODING) and by
    # Python's C-locale coercion (PEP 538: LC_CTYPE=C.UTF-8).
    assert set(report["env"]) <= {"__CF_USER_TEXT_ENCODING", "LC_CTYPE"}
    assert "disabled in the attachment scan process" in report["connect"]          # the in-process block
    assert "disabled in the attachment scan process" in report["dns"]
    assert report["write"] != "allowed" and not target.exists()
    assert report["core"] == [0, 0] and report["fsize"] == [0, 0]
    assert report["nofile"] == [scan.OPEN_FILES, scan.OPEN_FILES]
    assert report["cpu"] == [scan.CPU_SECONDS, scan.CPU_SECONDS + 5]
    assert report["mechanisms"]["watchdog"] is True
    assert report["stdin"] == 1000
    if sys.platform == "darwin":                  # the OS sandbox: no network even for native code, no fork
        assert report["os_connect_errno"] == 1                       # EPERM from the sandbox, not ECONNREFUSED (61)
        assert report["spawn"] != "allowed"


def test_the_child_command_is_an_isolated_interpreter():
    command = scan._child_command()
    at = command.index(sys.executable)
    assert command[at + 1:at + 5] == ["-I", "-B", "-X", "utf8"] and command[at + 5] == str(scan._CHILD)
    assert json.loads(command[at + 6]) == {"memory_bytes": scan.MEMORY_BYTES, "cpu_seconds": scan.CPU_SECONDS,
                                            "open_files": scan.OPEN_FILES}
    assert scan._child_env() == {}


def test_the_parent_never_imports_the_extraction_libraries():
    code = ("import sys; import nacre.ledger.scan_binary_attachment; "
            "print(sorted(m for m in ('pypdf', 'pypdfium2', 'PIL', 'rapidocr', 'onnxruntime', 'cv2') if m in sys.modules))")
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          env={**os.environ, "PYTHONPATH": str(SRC)}, check=True)
    assert done.stdout.strip() == "[]"


# ---- limits: every failure of the child is unscannable -----------------------------------------------------------
def test_timeout(monkeypatch):
    monkeypatch.setattr(scan, "TIMEOUT_S", 0.05)
    assert scan_binary_attachment(b"PK\x03\x04" + b"\0" * 100) == Unscannable("timeout after 0.05 s")


def test_memory_limit(monkeypatch):
    monkeypatch.setattr(scan, "MEMORY_BYTES", 30 * 1024 * 1024)       # below what the interpreter plus Pillow needs
    verdict = scan_binary_attachment(b"\x89PNG\r\n\x1a\n" + b"\0" * 100)
    assert verdict == Unscannable(f"memory limit of {30 * 1024 * 1024} bytes") or (
        verdict.reason.startswith("corrupt") and "MemoryError" in verdict.reason)     # RLIMIT_AS (Linux) path


def test_cpu_limit(monkeypatch):
    monkeypatch.setattr(scan, "CPU_SECONDS", 1)
    img = Image.new("RGB", (4000, 3000), "white")
    ImageDraw.Draw(img).text((10, 10), "hello world", fill="black")
    out = io.BytesIO()
    img.save(out, "PNG")
    assert scan_binary_attachment(out.getvalue()) == Unscannable("CPU limit")


@pytest.mark.skipif(sys.platform != "darwin", reason="the OS sandbox is macOS-only")
def test_a_missing_sandbox_fails_closed(monkeypatch):
    monkeypatch.setattr(scan, "SANDBOX_EXEC", "/nonexistent/sandbox-exec")
    assert scan_binary_attachment(b"PK\x03\x04") == Unscannable("network sandbox unavailable")


def test_a_crashing_child_is_unscannable(monkeypatch, tmp_path):
    crash = tmp_path / "crash.py"
    crash.write_text("import os, sys\nsys.stdin.buffer.read()\nos._exit(int(sys.argv[1]))\n")
    for code, reason in ((3, "scanner failed (exit 3)"), (MEMORY_EXIT_CODE, f"memory limit of {scan.MEMORY_BYTES} bytes")):
        monkeypatch.setattr(scan, "_child_command", lambda c=code: [sys.executable, str(crash), str(c)])
        assert scan_binary_attachment(b"x") == Unscannable(reason)
    killer = tmp_path / "kill.py"
    killer.write_text("import os, signal\nos.kill(os.getpid(), signal.SIGSEGV)\n")
    monkeypatch.setattr(scan, "_child_command", lambda: [sys.executable, str(killer)])
    assert scan_binary_attachment(b"x") == Unscannable("scanner failed (signal SIGSEGV)")


# ---- the JSON contract: anything unexpected is unscannable -----------------------------------------------------
@pytest.mark.parametrize("raw, verdict", [
    (b'{"verdict": "clean", "extractors": {"pypdf": "6.19.0"}}', Clean((("pypdf", "6.19.0"),))),
    (b'{"verdict": "secret", "rule_id": "github-pat", "location": "page 1"}', SecretDetected("github-pat", "page 1")),
    (b'{"verdict": "unscannable", "reason": "encrypted PDF"}', Unscannable("encrypted PDF")),
])
def test_valid_verdicts(raw, verdict):
    assert scan._parse(raw) == verdict


@pytest.mark.parametrize("raw", [
    b"", b"not json", b"[]", b'"clean"', b'{"verdict": "clean"}', b'{"verdict": "clean", "extractors": ["pypdf"]}',
    b'{"verdict": "clean", "extractors": {"pypdf": 6}}', b'{"verdict": "clean", "extractors": {}, "extra": 1}',
    b'{"verdict": "secret", "rule_id": "x"}', b'{"verdict": "secret", "rule_id": 1, "location": "p"}',
    b'{"verdict": "unscannable", "reason": null}', b'{"verdict": "ok"}',
    b'{"verdict": "unscannable", "reason": "' + b"x" * 70_000 + b'"}',
])
def test_invalid_replies_are_unscannable(raw):
    assert scan._parse(raw) == Unscannable("scanner failed (invalid verdict)")
