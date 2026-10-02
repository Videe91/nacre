"""
Functionality: Embed texts through the isolated embedder worker process, end to end (the main process's Embedder).
Owns: starting the worker (sandbox on macOS, empty environment, isolated interpreter, pipes), the start handshake
  (pin errors and the embedder id), splitting a call into request frames, deadlines on every pipe read and write,
  strict checking of each reply, serialising requests (one lock, one worker), restart-once-and-retry on failure, and
  stopping the worker.
Public entry: WorkerEmbedder, EmbedderWorkerError
Decisions: D-0024, D-0027, D-0006
Assumptions: A-0034
Notes: D-0024 amendment 1 (owner): the ONNX embedding runs in a separate long-lived worker with no network and no
  database credentials; the main process sends texts and receives vectors; the worker is restarted on failure.
  The main process never imports onnxruntime or tokenizers: this file imports only embed_local's pins and constants
  (whose runtime imports are inside LocalEmbedder) and run_embedder_worker's wire format (standard library only).
  - The worker: `[SANDBOX_EXEC -p SANDBOX_PROFILE] sys.executable -I -B -X utf8 recall/run_embedder_worker.py
    <limits> <model dir>`, env = {} (no PG*, NACRE_*, OPENAI_*, ANTHROPIC_*, proxy or other variable reaches it),
    cwd "/", stderr discarded (a library message could quote a text), close_fds. The model directory is resolved
    HERE (default_model_dir() reads NACRE_EMBEDDER_DIR in the main process) and passed as an argument.
  - Sandbox (D1, same profile as ledger/scan_binary_attachment.py, D-0027 amendment 3): no network of any kind, no
    file writes except /dev/null, no fork. On macOS a missing sandbox-exec fails closed (EmbedderWorkerError).
  - Limits (D1): MEMORY_BYTES = 4 GiB (RLIMIT_AS, Linux only; not measured on Linux yet), OPEN_FILES = 64.
  - Deadlines (D1): START_TIMEOUT_S = 60 (load measured at well under 1 s), REQUEST_TIMEOUT_S = 120 per chunk of
    CHUNK = 256 texts (about 0.5 s measured). A missed deadline counts as a worker failure.
  - Failure and restart (D1): a worker failure is the worker being dead, a pipe error, a missed deadline, a reply that
    is not exactly TAG_VECTORS + len(chunk) * dim float32, or a TAG_ERROR reply. On a failure the worker is killed
    and the call is retried ONCE in a freshly started worker; a second failure raises EmbedderWorkerError. A worker
    that died between calls is simply restarted by the next call (that start is the call's first attempt). A pin
    error (TAG_PIN_ERROR) raises EmbedderPinError at once: a restart cannot fix an altered model file.
  - Any other exception during a request (KeyboardInterrupt, the frame cap) also kills the worker, so a late reply
    is never read as the answer to the next request.
  - The whole call (all chunks) holds one lock, so calls from several threads are serialised and never interleave
    frames. After a fork, a child process never uses its parent's worker: it starts its own.
  - Input is checked here (a sequence of str, each encodable as UTF-8; LocalEmbedder raises TypeError for both too)
    before anything is sent; an empty call returns (0, dim) without
    starting the worker.
  Mutation run 2026-10-02 (run_mutants.py, 3 rounds): one survivor, `for attempt in (1, 2)` -> `(1, 2, 3)`, is
  EQUIVALENT: attempt 2 raises before a third attempt can run.
"""
import atexit
import json
import os
import select
import subprocess
import sys
import threading
import time
from collections.abc import Sequence
from pathlib import Path

import numpy as np

from nacre.recall.embed_local import DIM, EMBEDDER_ID, EmbedderPinError, default_model_dir
from nacre.recall.run_embedder_worker import HEADER, MAX_FRAME, TAG_PIN_ERROR, TAG_READY, TAG_VECTORS

_CHILD = Path(__file__).resolve().parent / "run_embedder_worker.py"
SANDBOX_EXEC = "/usr/bin/sandbox-exec"
SANDBOX_PROFILE = ('(version 1)(allow default)(deny network*)(deny file-write*)'
                   '(allow file-write-data (literal "/dev/null"))(deny process-fork)')
MEMORY_BYTES, OPEN_FILES = 4 * 1024 ** 3, 64
START_TIMEOUT_S, REQUEST_TIMEOUT_S, CHUNK = 60.0, 120.0, 256


class EmbedderWorkerError(RuntimeError):
    """The embedder worker failed twice in one call, or cannot be started safely."""


class _WorkerFailed(Exception):
    pass


class WorkerEmbedder:
    embedder_id = EMBEDDER_ID
    dim = DIM

    def __init__(self, model_dir: Path | None = None, *, start_timeout_s: float = START_TIMEOUT_S,
                 request_timeout_s: float = REQUEST_TIMEOUT_S):
        self._model_dir = Path(model_dir) if model_dir else default_model_dir()
        self._start_timeout, self._request_timeout = start_timeout_s, request_timeout_s
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._owner = os.getpid()
        atexit.register(self.close)

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        texts = list(texts)
        if not all(isinstance(t, str) for t in texts):
            raise TypeError("embed() takes a sequence of str")
        for t in texts:
            try:
                t.encode("utf-8")
            except UnicodeEncodeError:     # a lone surrogate: LocalEmbedder (tokenizers) raises TypeError too
                raise TypeError("embed() takes str that can be encoded as UTF-8") from None
        if not texts:
            return np.empty((0, DIM), dtype=np.float32)
        with self._lock:
            for attempt in (1, 2):
                try:
                    return self._embed_all(texts)
                except _WorkerFailed as exc:
                    self._stop()
                    if attempt == 2:
                        raise EmbedderWorkerError(f"the embedder worker failed twice: {exc}") from None
                except BaseException:          # e.g. KeyboardInterrupt mid-request: a late reply must never be read
                    self._stop()
                    raise

    def close(self) -> None:
        with self._lock:
            self._stop()

    @property
    def pid(self) -> int | None:
        """The running worker's pid (for tests and diagnostics), or None."""
        proc = self._proc
        return proc.pid if proc is not None and self._owner == os.getpid() and proc.poll() is None else None

    def _embed_all(self, texts: list[str]) -> np.ndarray:
        proc = self._running()
        out = np.empty((len(texts), DIM), dtype=np.float32)
        for start in range(0, len(texts), CHUNK):
            chunk = texts[start:start + CHUNK]
            body = json.dumps(chunk, ensure_ascii=True).encode("ascii")
            if len(body) > MAX_FRAME:
                raise ValueError(f"texts {start}..{start + len(chunk) - 1} exceed the {MAX_FRAME}-byte frame cap")
            deadline = time.monotonic() + self._request_timeout
            _send(proc, body, deadline)
            reply = _receive(proc, deadline)
            if reply[:1] != TAG_VECTORS or len(reply) != 1 + len(chunk) * DIM * 4:
                raise _WorkerFailed(f"unexpected reply (tag {reply[:1]!r}, {len(reply)} bytes)")
            out[start:start + len(chunk)] = np.frombuffer(reply, dtype="<f4", offset=1).reshape(len(chunk), DIM)
        return out

    def _running(self) -> subprocess.Popen:
        if self._owner != os.getpid():        # forked: the parent's worker and pipes are not ours
            self._proc, self._owner = None, os.getpid()
        if self._proc is not None and self._proc.poll() is None:
            return self._proc
        self._stop()
        return self._start()

    def _start(self) -> subprocess.Popen:
        if sys.platform == "darwin" and not Path(SANDBOX_EXEC).is_file():
            raise EmbedderWorkerError("network sandbox unavailable; refusing to start the embedder worker")
        proc = subprocess.Popen(_command(self._model_dir), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, env=_env(), cwd="/", close_fds=True, bufsize=0)
        self._proc = proc
        reply = _receive(proc, time.monotonic() + self._start_timeout)
        if reply[:1] == TAG_PIN_ERROR:
            self._stop()
            raise EmbedderPinError(reply[1:].decode(errors="replace"))
        try:
            ready = json.loads(reply[1:]) if reply[:1] == TAG_READY else None
        except ValueError:
            ready = None
        if ready != {"embedder_id": EMBEDDER_ID, "dim": DIM}:
            raise _WorkerFailed("the worker did not report the pinned embedder")
        return proc

    def _stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None or self._owner != os.getpid():
            return
        for pipe in (proc.stdin, proc.stdout):
            try:
                pipe.close()
            except OSError:
                pass
        if proc.poll() is None:
            proc.kill()
        proc.wait()


def _command(model_dir: Path) -> list[str]:
    limits = {"memory_bytes": MEMORY_BYTES, "open_files": OPEN_FILES}
    command = [sys.executable, "-I", "-B", "-X", "utf8", str(_CHILD), json.dumps(limits), str(model_dir)]
    return [SANDBOX_EXEC, "-p", SANDBOX_PROFILE, *command] if sys.platform == "darwin" else command


def _env() -> dict[str, str]:
    return {}


def _send(proc: subprocess.Popen, body: bytes, deadline: float) -> None:
    data = memoryview(HEADER.pack(len(body)) + body)
    fd = proc.stdin.fileno()
    os.set_blocking(fd, False)
    while data:
        _wait(fd, deadline, write=True)
        try:
            data = data[os.write(fd, data):]
        except BlockingIOError:
            continue
        except OSError as exc:              # BrokenPipeError: the worker is gone
            raise _WorkerFailed(f"write failed: {exc}") from None


def _receive(proc: subprocess.Popen, deadline: float) -> bytes:
    fd = proc.stdout.fileno()
    (size,) = HEADER.unpack(_read_exact(fd, HEADER.size, deadline))
    if size > MAX_FRAME or size == 0:
        raise _WorkerFailed(f"reply frame of {size} bytes")
    return _read_exact(fd, size, deadline)


def _read_exact(fd: int, n: int, deadline: float) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        _wait(fd, deadline, write=False)
        chunk = os.read(fd, min(n - len(buf), 1 << 20))
        if not chunk:
            raise _WorkerFailed("the worker closed its output")
        buf += chunk
    return bytes(buf)


def _wait(fd: int, deadline: float, *, write: bool) -> None:
    poller = select.poll()                  # poll, not select: no FD_SETSIZE limit in a process with many fds
    poller.register(fd, select.POLLOUT if write else select.POLLIN)
    if not poller.poll(max(deadline - time.monotonic(), 0) * 1000):
        raise _WorkerFailed("deadline exceeded")
