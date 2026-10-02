"""Tests for the isolated embedder worker (D-0024 amendment 1): recall/embedder_worker.py (client) and
recall/run_embedder_worker.py (the worker). Equivalence with the frozen reference vectors through the worker; the
worker's environment, rlimits and network/write/fork refusal (a probe launched through the real start path); the main
process never imports onnxruntime or tokenizers; restart once after a failure, raise on the second; serialised
threads; pin refusal; fail closed without the sandbox; single-query latency.
The model files must be fetched first: .venv/bin/python scripts/fetch_embedder.py"""
import json
import os
import shutil
import signal
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest

import nacre.recall.embedder_worker as ew
from nacre.recall.embed_local import DIM, FILES, EmbedderPinError, LocalEmbedder, default_model_dir
from nacre.recall.embedder_worker import EmbedderWorkerError, WorkerEmbedder
from nacre.recall.run_embedder_worker import MAX_FRAME, read_frame, write_frame

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
REF = json.loads((ROOT / "tests/recall/data/minilm_reference_vectors.json").read_text())

PROBE = """
import ctypes, json, os, resource, socket, struct, subprocess, sys
sys.path.insert(0, {src!r})
from nacre.recall.run_embedder_worker import TAG_PIN_ERROR, WorkerLimits, isolate_embedder_process, write_frame
mechanisms = isolate_embedder_process(WorkerLimits(**json.loads(sys.argv[1])))
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
    "env": dict(os.environ), "mechanisms": mechanisms, "argv": sys.argv[1:],
    "core": resource.getrlimit(resource.RLIMIT_CORE), "fsize": resource.getrlimit(resource.RLIMIT_FSIZE),
    "nofile": resource.getrlimit(resource.RLIMIT_NOFILE),
    "connect": attempt(lambda: socket.create_connection(("127.0.0.1", 9), timeout=1)),
    "dns": attempt(lambda: socket.getaddrinfo("localhost", 80)), "os_connect_errno": os_connect(),
    "dns_c": attempt(lambda: socket.gethostbyname("localhost")),    # the C function re-exported by socket
    "write": attempt(write_file), "spawn": attempt(lambda: subprocess.run(["/bin/echo"], capture_output=True)),
}}
write_frame(sys.stdout.buffer, TAG_PIN_ERROR + json.dumps(report).encode())
"""


@pytest.fixture(scope="module")
def worker():
    w = WorkerEmbedder()
    try:
        w.embed(["warm up"])
    except EmbedderPinError as e:
        pytest.fail(f"{e} (the embedder gate needs the pinned files; never skipped)")
    yield w
    w.close()


def _script(tmp_path, name, body):
    path = tmp_path / name
    path.write_text(body.format(src=str(SRC)))
    return path


# ---- equivalence and contract, through the worker ----

def test_worker_output_matches_the_frozen_reference_vectors(worker):
    got = worker.embed([i["text"] for i in REF["items"]])
    want = np.array([i["vector"] for i in REF["items"]], dtype=np.float32)
    cos = (got * want).sum(axis=1)
    assert cos.min() >= 0.9999, [(REF["items"][i]["text"][:30], float(c)) for i, c in enumerate(cos) if c < 0.9999]


def test_shape_dtype_norm_determinism_and_chunking(worker):
    texts = [f"lesson {i}: pin the image digest for service-{i % 7}" for i in range(ew.CHUNK + 40)]  # > one frame
    texts[3] = "caf\u00e9 \u2603 \U0001f600"
    v = worker.embed(texts)
    assert v.shape == (len(texts), DIM) and v.dtype == np.float32
    assert np.allclose(np.linalg.norm(v, axis=1), 1.0, atol=1e-5)
    assert np.allclose(v[[0, 3, ew.CHUNK + 39]], worker.embed([texts[0], texts[3], texts[-1]]), atol=1e-5)
    assert worker.embed([]).shape == (0, DIM)
    assert worker.embedder_id == LocalEmbedder.embedder_id and worker.dim == DIM


def test_worker_matches_the_in_process_embedder():
    texts = ["alpha", "beta gamma", "", "word " * 2000]
    local, w = LocalEmbedder(), WorkerEmbedder()
    try:
        assert np.allclose(w.embed(texts), local.embed(texts), atol=1e-6)
    finally:
        w.close()


def test_non_str_input_is_refused_before_anything_is_sent(worker):
    pid = worker.pid
    with pytest.raises(TypeError):
        worker.embed([b"bytes"])
    with pytest.raises(TypeError):
        worker.embed(["lone \ud800 surrogate"])            # LocalEmbedder raises TypeError for it too
    with pytest.raises(TypeError):
        LocalEmbedder().embed(["lone \ud800 surrogate"])
    assert worker.pid == pid


# ---- isolation ----

def test_the_worker_gets_no_environment_no_network_no_writes_and_hard_limits(tmp_path, monkeypatch):
    target = tmp_path / "written-by-worker"
    probe = tmp_path / "probe.py"
    probe.write_text(PROBE.format(src=str(SRC), target=str(target)))
    for name in ("PGPASSWORD", "PGHOST", "NACRE_DSN", "NACRE_EMBEDDER_DIR", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
                 "HTTPS_PROXY"):
        monkeypatch.setenv(name, str(default_model_dir()) if name == "NACRE_EMBEDDER_DIR" else "must-not-reach")
    monkeypatch.setattr(ew, "_CHILD", probe)
    w = WorkerEmbedder()
    with pytest.raises(EmbedderPinError) as info:     # the probe reports through the pin-error channel
        w.embed(["x"])
    report = json.loads(str(info.value))
    assert set(report["env"]) <= {"__CF_USER_TEXT_ENCODING", "LC_CTYPE"}   # set by macOS / PEP 538, not by us
    assert report["argv"][1] == str(default_model_dir())                    # resolved in the parent, passed as arg
    assert "disabled in the embedder worker" in report["connect"]
    assert "disabled in the embedder worker" in report["dns"]
    assert "disabled in the embedder worker" in report["dns_c"]
    assert report["write"] != "allowed" and not target.exists()
    assert report["core"] == [0, 0] and report["fsize"] == [0, 0]
    assert report["nofile"] == [ew.OPEN_FILES, ew.OPEN_FILES]
    if sys.platform == "darwin":                  # the OS sandbox: no network even for native code, no fork
        assert report["os_connect_errno"] == 1                       # EPERM from the sandbox, not ECONNREFUSED (61)
        assert report["spawn"] != "allowed"
    else:
        assert report["mechanisms"]["rlimit_as"] is True


def test_the_worker_command_is_an_isolated_interpreter_under_the_sandbox():
    command = ew._command(Path("/models"))
    at = command.index(sys.executable)
    assert command[at + 1:at + 5] == ["-I", "-B", "-X", "utf8"] and command[at + 5] == str(ew._CHILD)
    assert json.loads(command[at + 6]) == {"memory_bytes": ew.MEMORY_BYTES, "open_files": ew.OPEN_FILES}
    assert command[at + 7] == "/models" and ew._env() == {}
    if sys.platform == "darwin":
        assert command[:3] == [ew.SANDBOX_EXEC, "-p", ew.SANDBOX_PROFILE]
        assert "(deny network*)" in ew.SANDBOX_PROFILE and "(deny process-fork)" in ew.SANDBOX_PROFILE


@pytest.mark.skipif(sys.platform != "darwin", reason="the OS sandbox is macOS-only")
def test_a_missing_sandbox_fails_closed(monkeypatch):
    monkeypatch.setattr(ew, "SANDBOX_EXEC", "/nonexistent/sandbox-exec")
    with pytest.raises(EmbedderWorkerError, match="sandbox unavailable"):
        WorkerEmbedder().embed(["x"])


def test_the_main_process_never_imports_onnxruntime_or_tokenizers():
    code = ("import sys; from nacre.recall.index_version import default_embedder; "
            "e = default_embedder(); v = e.embed(['offline']); assert v.shape == (1, 384); e.close(); "
            "print(sorted(m for m in sys.modules if m.split('.')[0] in ('onnxruntime', 'tokenizers')))")
    done = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True,
                          env={**os.environ, "PYTHONPATH": str(SRC)}, check=True)
    assert done.stdout.strip() == "[]"


def test_default_embedder_is_the_worker():
    from nacre.recall.index_version import default_embedder
    assert isinstance(default_embedder(), WorkerEmbedder)


@pytest.mark.parametrize("damage", ["tamper_model", "missing"])
def test_a_missing_or_altered_model_file_is_refused_by_the_worker(tmp_path, damage):
    for name in FILES:
        shutil.copy(default_model_dir() / name, tmp_path / name)
    if damage == "missing":
        (tmp_path / "tokenizer.json").unlink()
    else:
        (tmp_path / "model.onnx").write_bytes((tmp_path / "model.onnx").read_bytes() + b" ")
    with pytest.raises(EmbedderPinError):
        WorkerEmbedder(tmp_path).embed(["x"])


def test_library_output_on_stdout_inside_the_worker_never_corrupts_a_frame(monkeypatch, worker):
    # The real worker main(), with LocalEmbedder.embed wrapped to print to stdout first (as a chatty native library
    # or a stray print could): fd 1 is /dev/null in the worker, so the frames stay intact.
    code = ("import sys; sys.path.insert(0, %r); import nacre.recall.embed_local as el; real = el.LocalEmbedder.embed; "
            "el.LocalEmbedder.embed = lambda self, t: (print('noise ' * 50, flush=True), real(self, t))[1]; "
            "from nacre.recall.run_embedder_worker import main; main()") % str(SRC)
    real_command = ew._command
    monkeypatch.setattr(ew, "_command", lambda d: [c if c != str(ew._CHILD) else "-c" for c in real_command(d)][:-2]
                        + [code] + real_command(d)[-2:])
    w = WorkerEmbedder()
    try:
        assert np.allclose(w.embed(["noisy"]), worker.embed(["noisy"]), atol=1e-6)
    finally:
        w.close()


# ---- failure and restart ----

def test_a_killed_worker_is_restarted_by_the_next_call(worker):
    before = worker.embed(["restart me"])
    old = worker.pid
    os.kill(old, signal.SIGKILL)
    time.sleep(0.2)
    assert np.allclose(worker.embed(["restart me"]), before, atol=1e-6)
    assert worker.pid not in (None, old)


def _counting_popen(monkeypatch):
    starts = []
    real = subprocess.Popen

    def popen(*a, **k):
        starts.append(1)
        return real(*a, **k)
    monkeypatch.setattr(ew.subprocess, "Popen", popen)
    return starts


DIES_AFTER_READY = """
import json, sys
sys.path.insert(0, {src!r})
from nacre.recall.run_embedder_worker import TAG_READY, read_frame, write_frame
from nacre.recall.embed_local import DIM, EMBEDDER_ID
write_frame(sys.stdout.buffer, TAG_READY + json.dumps({{"embedder_id": EMBEDDER_ID, "dim": DIM}}).encode())
read_frame(sys.stdin.buffer)
sys.exit(3)
"""

HANGS_AFTER_READY = DIES_AFTER_READY.replace("sys.exit(3)", "import time; time.sleep(60)")
WRONG_REPLY = DIES_AFTER_READY.replace("sys.exit(3)", "write_frame(sys.stdout.buffer, b'V' + b'\\0' * 8)")
WRONG_ID = (DIES_AFTER_READY.replace('"embedder_id": EMBEDDER_ID', '"embedder_id": "another-model"')
            .replace("read_frame(sys.stdin.buffer)\nsys.exit(3)",      # otherwise a perfect worker: only the id is wrong
                     "while (b := read_frame(sys.stdin.buffer)) is not None:\n"
                     "    write_frame(sys.stdout.buffer, b'V' + b'\\0' * (4 * DIM * len(json.loads(b))))"))


@pytest.mark.parametrize("body", [DIES_AFTER_READY, HANGS_AFTER_READY, WRONG_REPLY, WRONG_ID],
                         ids=["dies", "hangs", "wrong-reply", "wrong-embedder"])
def test_a_second_failure_in_one_call_raises_after_exactly_one_restart(tmp_path, monkeypatch, body):
    monkeypatch.setattr(ew, "_CHILD", _script(tmp_path, "bad.py", body))
    starts = _counting_popen(monkeypatch)
    w = WorkerEmbedder(request_timeout_s=1.0)
    with pytest.raises(EmbedderWorkerError, match="failed twice"):
        w.embed(["x"])
    assert len(starts) == 2 and w.pid is None


def test_one_failure_is_retried_transparently(tmp_path, monkeypatch, worker):
    real_child = ew._CHILD
    bad = _script(tmp_path, "bad.py", DIES_AFTER_READY)
    starts = _counting_popen(monkeypatch)
    calls = []

    def command(model_dir):                       # first start: a worker that dies; the restart: the real one
        calls.append(1)
        monkeypatch.setattr(ew, "_CHILD", bad if len(calls) == 1 else real_child)
        return real_command(model_dir)
    real_command = ew._command
    monkeypatch.setattr(ew, "_command", command)
    w = WorkerEmbedder()
    try:
        assert np.allclose(w.embed(["retry"]), worker.embed(["retry"]), atol=1e-6)
        assert len(starts) == 2
    finally:
        w.close()


def test_an_interrupted_request_kills_the_worker_so_no_late_reply_is_read(worker, monkeypatch):
    worker.embed(["a"])
    old = worker.pid

    def interrupted(*a, **k):
        raise KeyboardInterrupt
    monkeypatch.setattr(ew, "_receive", interrupted)
    with pytest.raises(KeyboardInterrupt):
        worker.embed(["b"])
    monkeypatch.undo()
    assert worker.pid is None
    assert worker.embed(["a"]).shape == (1, DIM) and worker.pid != old


def test_threads_are_serialised_and_get_their_own_answers(worker):
    texts = [f"thread {i} remembers lesson {i * 7}" for i in range(16)]
    want = worker.embed(texts)
    got, errors = {}, []

    def run(i):
        try:
            for _ in range(5):
                got[i] = worker.embed([texts[i]])[0]
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
    threads = [threading.Thread(target=run, args=(i,)) for i in range(len(texts))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and all(np.allclose(got[i], want[i], atol=1e-5) for i in range(len(texts)))


# ---- wire format ----

def test_frames_round_trip_and_oversized_or_truncated_frames_are_refused():
    import io
    buf = io.BytesIO()
    write_frame(buf, b"Vabc")
    buf.seek(0)
    assert read_frame(buf) == b"Vabc" and read_frame(buf) is None
    with pytest.raises(ValueError):
        read_frame(io.BytesIO((MAX_FRAME + 1).to_bytes(4, "big")))
    with pytest.raises(ValueError):
        read_frame(io.BytesIO((10).to_bytes(4, "big") + b"short"))
    with pytest.raises(ValueError):
        write_frame(io.BytesIO(), b"x" * (MAX_FRAME + 1))


# ---- latency (D-0025: 150 ms warm recall); timing-sensitive, so `pytest -m bench` on a quiet machine ----

def _latency(embed, n=200):
    for i in range(20):
        embed([f"warm up {i}"])
    times = []
    for i in range(n):
        t = time.perf_counter()
        embed([f"how do we pin the base image for service {i}?"])
        times.append((time.perf_counter() - t) * 1000)
    return statistics.median(times), statistics.quantiles(times, n=20)[-1]


@pytest.mark.bench
def test_single_query_latency_through_the_worker_vs_in_process(worker):
    w_med, w_p95 = _latency(worker.embed)
    l_med, l_p95 = _latency(LocalEmbedder().embed)
    print(f"\nsingle query: worker median {w_med:.2f} ms p95 {w_p95:.2f} ms; "
          f"in-process median {l_med:.2f} ms p95 {l_p95:.2f} ms")
    assert w_med < 50 and w_p95 < 150, (w_med, w_p95)
