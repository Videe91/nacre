"""
Functionality: Decide whether one binary attachment may be stored, end to end: run the extraction and detection in
  an isolated child process, enforce its wall-clock timeout, and turn its answer into a verdict.
Owns: the verdict types (clean with extractor versions / secret with rule and location / unscannable with reason),
  starting the child (empty environment, isolated interpreter, input on stdin), the timeout, the limits handed to
  the child, mapping every failure of the child to an unscannable verdict, strict parsing of the child's JSON, and
  the rejection message.
Public entry: scan_binary_attachment(), rejection_message(), Clean, SecretDetected, Unscannable, Verdict
Decisions: D-0027, D-0006, D-0008
Assumptions: A-0021, A-0041
Notes: D-0027 §2; D-0006 amendment 3 (owner condition: isolated subprocess, no network, no database credentials,
  memory and CPU limits, a timeout). The extraction libraries are never imported in this process.
  - The child: `sys.executable -I -B -X utf8 ledger/run_binary_scan_child.py <limits>`, env = {} (so no PG*, NACRE_*,
    OPENAI_*, ANTHROPIC_*, proxy or any other variable reaches it), cwd "/", stdin = the attachment, stderr discarded
    (a library message could quote content), close_fds. The child locks itself down first
    (ledger/isolate_scan_process.py) and answers with one JSON line.
  - D1, OS-level sandbox on macOS: the child runs under `/usr/bin/sandbox-exec` with SANDBOX_PROFILE (no network of
    any kind, no file writes except /dev/null, no fork). Why: the Python-level socket block cannot stop native code,
    and onnxruntime 1.30.0's macOS wheel carries Microsoft 1DS telemetry that starts NSURLSession traffic during
    plain CPU inference (observed 2026-10-02: "telemetry.cc ... Failed to persist telemetry" under the sandbox;
    NSURLCache writes without it). On macOS a missing sandbox-exec fails closed (unscannable). On other platforms
    only the in-process block applies (no OS network isolation yet; flagged to the owner).
  - D1 limits (measured 2026-10-02, Darwin arm64, see the R5 isolation report): one 25 MP image (the per-image cap)
    peaks at ~3.1 GB resident and ~16 CPU-seconds in 2.8 s wall (onnxruntime uses every core). So MEMORY_BYTES = 4 GiB,
    TIMEOUT_S = 120 s wall, CPU_SECONDS = 600 (about 5 cores busy for the whole timeout), OPEN_FILES = 64.
    Consequence: an attachment at all the OCR caps at once (64 images of 25 MP) cannot finish in 120 s and is
    rejected as unscannable(timeout); flagged to the owner.
  - Fail closed: timeout -> unscannable(timeout after N s); memory watchdog exit -> unscannable(memory limit);
    killed by a signal (SIGXCPU = CPU limit) or any other exit, empty or malformed output -> unscannable(scanner
    failed ...). A verdict is accepted only if every field has the expected type.
  - Any finding -> SecretDetected; the matched value never leaves the child. The declared media type is accepted
    but never consulted: content decides (D-0008 amendment 6).
  - Clean.extractors is a sorted tuple of (name, version) pairs (immutable); append_event writes it as the
    attachment map's `extractors` (D-0008 amendment 7).
"""
import json
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from nacre.ledger.isolate_scan_process import MEMORY_EXIT_CODE

TIMEOUT_S, CPU_SECONDS, MEMORY_BYTES, OPEN_FILES = 120, 600, 4 * 1024 ** 3, 64
_CHILD = Path(__file__).resolve().parent / "run_binary_scan_child.py"
SANDBOX_EXEC = "/usr/bin/sandbox-exec"
SANDBOX_PROFILE = ('(version 1)(allow default)(deny network*)(deny file-write*)'
                   '(allow file-write-data (literal "/dev/null"))(deny process-fork)')
_MAX_REPLY = 64 * 1024


@dataclass(frozen=True, slots=True)
class Clean:
    extractors: tuple[tuple[str, str], ...]     # (name, version) of every extractor that ran


@dataclass(frozen=True, slots=True)
class SecretDetected:
    rule_id: str
    location: str


@dataclass(frozen=True, slots=True)
class Unscannable:
    reason: str


type Verdict = Clean | SecretDetected | Unscannable


def scan_binary_attachment(data: bytes, declared_media_type: str | None = None) -> Verdict:
    """The verdict for `data`. `declared_media_type` is ignored for the decision: content decides."""
    if type(data) is not bytes:
        raise TypeError("attachments are bytes")
    if sys.platform == "darwin" and not Path(SANDBOX_EXEC).is_file():
        return Unscannable("network sandbox unavailable")
    try:
        done = subprocess.run(_child_command(), input=data, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              env=_child_env(), cwd="/", close_fds=True, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:          # subprocess.run has already killed and reaped the child
        return Unscannable(f"timeout after {TIMEOUT_S} s")
    if done.returncode == MEMORY_EXIT_CODE:
        return Unscannable(f"memory limit of {MEMORY_BYTES} bytes")
    if done.returncode < 0:
        try:
            name = signal.Signals(-done.returncode).name
        except ValueError:
            name = str(-done.returncode)
        return Unscannable("CPU limit" if name == "SIGXCPU" else f"scanner failed (signal {name})")
    if done.returncode != 0:
        return Unscannable(f"scanner failed (exit {done.returncode})")
    return _parse(done.stdout)


def rejection_message(verdict: Verdict) -> str:
    """The D-0027 error text for a rejected verdict. It never contains a matched value."""
    match verdict:
        case SecretDetected(rule_id, location):
            return f"attachment_rejected: secret_detected (rule {rule_id}, at {location})"
        case Unscannable(reason):
            return f"attachment_rejected: unscannable({reason})"
    raise ValueError("a clean verdict is not a rejection")


def _child_command() -> list[str]:
    limits = {"memory_bytes": MEMORY_BYTES, "cpu_seconds": CPU_SECONDS, "open_files": OPEN_FILES}
    command = [sys.executable, "-I", "-B", "-X", "utf8", str(_CHILD), json.dumps(limits)]
    return [SANDBOX_EXEC, "-p", SANDBOX_PROFILE, *command] if sys.platform == "darwin" else command


def _child_env() -> dict[str, str]:
    return {}


def _parse(raw: bytes) -> Verdict:
    bad = Unscannable("scanner failed (invalid verdict)")
    if len(raw) > _MAX_REPLY:
        return bad
    try:
        reply = json.loads(raw)
    except ValueError:
        return bad
    if type(reply) is not dict:
        return bad
    match reply:
        case {"verdict": "clean", "extractors": dict() as found} if len(reply) == 2 and all(
                type(k) is str and type(v) is str for k, v in found.items()):
            return Clean(tuple(sorted(found.items())))
        case {"verdict": "secret", "rule_id": str() as rule_id, "location": str() as location} if len(reply) == 3:
            return SecretDetected(rule_id, location)
        case {"verdict": "unscannable", "reason": str() as reason} if len(reply) == 2:
            return Unscannable(reason)
    return bad
