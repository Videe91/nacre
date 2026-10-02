"""
Functionality: The isolated embedder worker process, end to end: lock itself down, load the pinned model, then answer
  embedding requests over its stdin/stdout pipes until stdin closes.
Owns: the worker's lockdown (rlimits, Python socket/DNS entry points disabled) done FIRST, before onnxruntime or
  tokenizers is imported; moving the protocol off fd 1 so no library output can corrupt a frame; the wire format
  (length-prefixed frames, tags, the frame size cap); validating each request; and the reply frames.
Public entry: main(), isolate_embedder_process(), WorkerLimits, read_frame(), write_frame(), TAG_READY, TAG_VECTORS,
  TAG_PIN_ERROR, TAG_ERROR, MAX_FRAME
Decisions: D-0024, D-0027, D-0006
Assumptions: A-0034
Notes: D-0024 amendment 1 (owner): the local embedder runs in a separate long-lived worker with no network and no
  database credentials. Started by recall/embedder_worker.py as
  `[sandbox-exec -p <profile>] python -I -B -X utf8 <this file> <limits json> <model dir>` with env = {}, cwd "/",
  stderr discarded. -I ignores PYTHON* variables, the user site and the current directory, so sys.path gets the
  source root explicitly (computed from this file, never from the environment).
  - Wire format (D1): every frame is a 4-byte big-endian length, then that many bytes: one tag byte and a body.
      worker -> client, once at start:  TAG_READY + JSON {"embedder_id": str, "dim": int}
                                        or TAG_PIN_ERROR + UTF-8 message (a model file is missing or altered), then exit
      client -> worker, per request:    JSON list of str (ensure_ascii, so lone surrogates round-trip unchanged)
      worker -> client, per request:    TAG_VECTORS + n * dim little-endian float32 (row-major), n = len(request)
                                        or TAG_ERROR + the exception's type name (a malformed request or an
                                        embedding error; never the message, which could quote a text), then exit
    A frame longer than MAX_FRAME (64 MiB) is refused by either side. The client splits large calls into chunks.
  - D1 lockdown (why not ledger/isolate_scan_process.py: its RLIMIT_CPU caps the whole process lifetime, which is
    right for one scan and wrong for a worker that lives as long as the main process):
      RLIMIT_CORE = 0 (a crash never dumps texts to disk);
      RLIMIT_FSIZE = 0: measured 2026-10-02 (Darwin 25.6, onnxruntime 1.30.0): loading the model and 250 embed calls
        complete with RLIMIT_FSIZE 0, with and without the sandbox, so onnxruntime needs no file writes;
      RLIMIT_NOFILE = limits.open_files (64);
      RLIMIT_AS = limits.memory_bytes where the kernel accepts it (Linux; macOS rejects it). No watchdog: the worker
        parses no untrusted binary, only our own texts, truncated to 256 tokens.
      Python's socket/DNS entry points raise OSError. Native code is not stopped by that; on macOS the client runs the
      worker under sandbox-exec (no network, no writes, no fork). Measured 2026-10-02: under the sandbox onnxruntime's
      telemetry still tries to persist a device id even after disable_telemetry_events() ("Failed to persist
      telemetry device ID"), so the OS sandbox is doing real work. Elsewhere only the in-process block applies until
      Phase 3 gate item 17 (Linux isolation).
  - fd 1 is re-pointed at /dev/null after the protocol stream is dup'ed, so a stray print or native log on stdout can
    never be read as a frame.
"""
import _socket
import json
import os
import resource
import socket
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

HEADER = struct.Struct(">I")
MAX_FRAME = 64 * 1024 * 1024
TAG_READY, TAG_VECTORS, TAG_PIN_ERROR, TAG_ERROR = b"R", b"V", b"P", b"E"


@dataclass(frozen=True, slots=True)
class WorkerLimits:
    memory_bytes: int
    open_files: int


def isolate_embedder_process(limits: WorkerLimits) -> dict[str, bool]:
    """Apply the rlimits and the socket block to this process; returns whether RLIMIT_AS is active."""
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (limits.open_files, limits.open_files))
    try:
        resource.setrlimit(resource.RLIMIT_AS, (limits.memory_bytes, limits.memory_bytes))
        address_space = True
    except (ValueError, OSError):         # macOS: not supported
        address_space = False
    _block_network()
    return {"rlimit_as": address_space}


def _refuse(*args, **kwargs):
    raise OSError("network access is disabled in the embedder worker")


class _NoSocket(socket.socket):
    def __init__(self, *args, **kwargs):
        _refuse()


def _block_network() -> None:
    socket.socket = socket.SocketType = _NoSocket
    _socket.socket = _socket.SocketType = _NoSocket
    for name in ("create_connection", "getaddrinfo", "gethostbyname", "gethostbyname_ex", "gethostbyaddr",
                 "socketpair", "fromfd", "create_server"):
        setattr(socket, name, _refuse)
        if hasattr(_socket, name):
            setattr(_socket, name, _refuse)


def read_frame(stream) -> bytes | None:
    """One frame body from a blocking binary stream; None on a clean end of stream before a header."""
    head = _read_exact(stream, HEADER.size)
    if head is None:
        return None
    (size,) = HEADER.unpack(head)
    if size > MAX_FRAME:
        raise ValueError(f"frame of {size} bytes exceeds {MAX_FRAME}")
    body = _read_exact(stream, size)
    if body is None:
        raise ValueError("stream ended inside a frame")
    return body


def _read_exact(stream, n: int) -> bytes | None:
    buf = bytearray()
    while len(buf) < n:
        chunk = stream.read(n - len(buf))
        if not chunk:
            if buf:
                raise ValueError("stream ended inside a frame")
            return None
        buf += chunk
    return bytes(buf)


def write_frame(stream, body: bytes) -> None:
    if len(body) > MAX_FRAME:
        raise ValueError(f"frame of {len(body)} bytes exceeds {MAX_FRAME}")
    stream.write(HEADER.pack(len(body)) + body)
    stream.flush()


def _texts(body: bytes) -> list[str]:
    texts = json.loads(body.decode("ascii"))
    if type(texts) is not list or not all(type(t) is str for t in texts):
        raise ValueError("a request is a JSON list of strings")
    return texts


def main() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))       # .../src
    isolate_embedder_process(WorkerLimits(**json.loads(sys.argv[1])))   # first: before the runtime is imported
    out = os.fdopen(os.dup(1), "wb")
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, 1)
    os.close(devnull)
    inp = sys.stdin.buffer
    from nacre.recall.embed_local import EmbedderPinError, LocalEmbedder
    try:
        emb = LocalEmbedder(Path(sys.argv[2]))
    except EmbedderPinError as exc:
        write_frame(out, TAG_PIN_ERROR + str(exc).encode())
        return
    write_frame(out, TAG_READY + json.dumps({"embedder_id": emb.embedder_id, "dim": emb.dim}).encode())
    import numpy as np
    while (body := read_frame(inp)) is not None:
        try:
            vectors = np.ascontiguousarray(emb.embed(_texts(body)), dtype="<f4")
        except Exception as exc:            # noqa: BLE001 - any refusal is reported, then the worker exits
            write_frame(out, TAG_ERROR + type(exc).__name__.encode())
            return
        write_frame(out, TAG_VECTORS + vectors.tobytes())


if __name__ == "__main__":
    main()
