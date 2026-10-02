"""
Functionality: Lock down the current process before it parses untrusted attachment bytes: resource limits, no
  network, and a memory watchdog, end to end.
Owns: the rlimits (CPU seconds, open files, no core dumps, no file writes, address space where the kernel enforces
  it), replacing every socket entry point with one that refuses, and the peak-memory watchdog thread.
Public entry: isolate_scan_process(), ScanLimits, MEMORY_EXIT_CODE
Decisions: D-0006, D-0027
Assumptions: A-0041
Notes: D-0006 amendment 3 (owner condition): extraction and OCR run in an isolated subprocess with no network, no
  database credentials, memory and CPU limits, and a timeout. The parent (ledger/scan_binary_attachment.py) owns the
  empty environment and the wall-clock timeout; this file owns what the child does to itself, FIRST, before any
  extraction library is imported. Standard library only.
  - D1 rlimits: RLIMIT_CPU = limits.cpu_seconds (soft; SIGXCPU kills, the hard limit 5 s later is SIGKILL);
    RLIMIT_NOFILE = limits.open_files; RLIMIT_CORE = 0 (a crash never writes a core holding attachment bytes);
    RLIMIT_FSIZE = 0 (the child never writes a file; a write raises SIGXFSZ and kills it).
  - Memory: RLIMIT_AS = limits.memory_bytes where the kernel accepts it (Linux). macOS rejects RLIMIT_AS/DATA/RSS
    (setrlimit raises "current limit exceeds maximum limit"; measured 2026-10-02, Darwin 25.6), so the watchdog thread
    is the enforcement there: every WATCHDOG_INTERVAL_S it reads the PEAK resident size (getrusage ru_maxrss: bytes
    on macOS, KiB on Linux) and exits with MEMORY_EXIT_CODE once it is above the limit. It runs on every platform.
    Limit of the watchdog: it cannot interrupt one native call that allocates far past the limit between two
    polls; the extraction caps (25 MP per image, 64 MiB decompressed) bound what one call can allocate.
  - Network: socket.socket, _socket.socket, create_connection, getaddrinfo, gethostbyname(_ex), socketpair and
    fromfd all raise OSError. This stops Python code (rapidocr's requests-based download branch, urllib); it does
    NOT stop native code. onnxruntime 1.30.0 (macOS wheel) carries Microsoft 1DS telemetry that uses the OS URL
    stack directly, so on macOS the parent also runs the child under an OS sandbox that denies all network
    (ledger/scan_binary_attachment.py). Elsewhere this in-process block is the only network control (flagged to the
    owner). No proxy variables exist: the parent starts the child with an empty env.
"""
import _socket
import os
import resource
import socket
import sys
import threading
from dataclasses import dataclass

MEMORY_EXIT_CODE = 86
WATCHDOG_INTERVAL_S = 0.02


@dataclass(frozen=True, slots=True)
class ScanLimits:
    memory_bytes: int
    cpu_seconds: int
    open_files: int


def isolate_scan_process(limits: ScanLimits) -> dict[str, bool]:
    """Apply every limit to this process; returns which memory mechanisms are active (for the evidence)."""
    _limit(resource.RLIMIT_CORE, 0)
    _limit(resource.RLIMIT_FSIZE, 0)
    _limit(resource.RLIMIT_NOFILE, limits.open_files)
    _limit(resource.RLIMIT_CPU, limits.cpu_seconds, limits.cpu_seconds + 5)
    try:
        _limit(resource.RLIMIT_AS, limits.memory_bytes)
        address_space = True
    except (ValueError, OSError):         # macOS: not supported; the watchdog enforces the limit
        address_space = False
    _block_network()
    threading.Thread(target=_watch_memory, args=(limits.memory_bytes,), daemon=True, name="scan-memory").start()
    return {"rlimit_as": address_space, "watchdog": True}


def _limit(which: int, soft: int, hard: int | None = None) -> None:
    resource.setrlimit(which, (soft, soft if hard is None else hard))


def _refuse(*args, **kwargs):
    raise OSError("network access is disabled in the attachment scan process")


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


def _peak_bytes() -> int:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak if sys.platform == "darwin" else peak * 1024


def _watch_memory(limit: int) -> None:
    stop = threading.Event()
    while not stop.wait(WATCHDOG_INTERVAL_S):
        if _peak_bytes() > limit:
            os._exit(MEMORY_EXIT_CODE)       # no cleanup, no output: the parent reads the exit code
