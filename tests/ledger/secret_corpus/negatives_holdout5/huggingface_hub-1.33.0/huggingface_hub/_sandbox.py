# coding=utf-8
# Copyright 2026-present, the HuggingFace Inc. team.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import hashlib
import hmac
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from secrets import token_hex
from typing import Any, BinaryIO, Callable, Iterator, List, Literal, overload
from urllib.parse import urlparse

import httpx

from . import constants
from ._sandbox_cache import (
    HOST_TRUST_TTL,
    CacheContext,
    CachedHost,
    delete_pool_cache,
    read_pool_cache,
    save_pool_cache,
)
from ._space_api import Volume
from .errors import HfHubHTTPError, SandboxCommandError, SandboxError
from .hf_api import HfApi, JobInfo
from .utils import get_token, logging
from .utils._parsing import parse_duration


logger = logging.get_logger(__name__)

# Port the sandbox server listens on inside the job. Deliberately uncommon so that typical user ports stay free.
SANDBOX_SERVER_PORT = 49983

# Stable marker present on every sandbox job to ease filtering
SANDBOX_LABEL = "hf-sandbox"
# Sandbox mode: "dedicated" or "pool" to ease filtering
MODE_LABEL = "hf-sandbox-mode"
MODE_DEDICATED = "dedicated"
MODE_POOL = "pool"
# Pool name, to scope host reuse to a named group (see SandboxPool(name=...)). Present on pool
# host jobs only. Pool config (capacity, idle timeout) lives in the host's env vars, read via
# inspect_job — labels are kept for filtering/grouping only.
POOL_LABEL = "hf-sandbox-pool"
# Per-job public nonce the sandbox token is derived from (see _derive_sandbox_token), so
# `Sandbox.connect(id)` can recompute the token from any machine with no local state.
NONCE_LABEL = "hf-sandbox-nonce"

RESERVED_SANDBOX_LABELS = frozenset({SANDBOX_LABEL, MODE_LABEL, POOL_LABEL, NONCE_LABEL})

DEFAULT_IMAGE = "python:3.12"

# The exact sbx-server build every sandbox job downloads and runs as root, pinned by digest.
# The bucket object is fetched by its sha256 name and the download is verified before it is
# made executable (see `_BOOTSTRAP_DOWNLOAD`), so the bytes that run are the bytes that were
# reviewed for this release -- not whatever the mutable `sbx-server` alias points at today.
# Both constants move together on every server release; the digest is printed by the server
# repo's publish workflow.
SANDBOX_SERVER_VERSION = "0.6.0"
SANDBOX_SERVER_SHA256 = "bd08d60b3bdab4ddd4e81401b6dacc96e01bcee3a4753e258b99d40c07d81ee3"

# The sbx-server wire contract this client drives, checked against `/health`'s `protocol` on
# startup. A pool host keeps the binary it downloaded at boot for up to 24h, so pinning a
# digest does not stop this client from meeting a server it did not pin -- the check does.
SANDBOX_SERVER_PROTOCOL = 2

DEFAULT_IDLE_TIMEOUT = 10 * 60  # 10 minutes
SANDBOX_MAX_LIFETIME = "24h"

DEFAULT_SANDBOXES_PER_HOST = 50

# How long `close()` waits for in-flight creates before giving up on them. A
# wedged create must not hang process exit, but returning immediately would let
# a host it is still booting escape teardown.
_CLOSE_DRAIN_TIMEOUT = 30.0

# Ceiling on the output `run()` will accumulate for its result. Without one, a
# runaway command (a loop printing to stdout, say) is an unbounded allocation in
# the caller's process -- and it happened even when a callback was already
# consuming the output. Raising beats being OOM-killed with no explanation.
MAX_CAPTURED_OUTPUT_CHARS = 64 * 1024 * 1024

# Which pool hosts `create()` may adopt from job labels. Labels are set by whoever
# creates a Job, so "any job carrying our pool's labels" is not an identity claim --
# see the `adopt_hosts` argument of [`SandboxPool`].
ADOPT_OWN = "own"
ADOPT_NAMESPACE = "namespace"
ADOPT_NEVER = "never"

SHARED_ID_SEP = "."

# Job stages in which a sandbox/host is finished and needs no teardown.
_TERMINAL_STAGES = ("COMPLETED", "ERROR", "DELETED", "CANCELED")

# Safety bound on create()'s pack-retry loop
_MAX_PACK_ROUNDS = 8

# hf-mount path where the server bucket is mounted on every sandbox job
_SERVER_MOUNT_PATH = "/.hf-sbx-server"

# Job startup script (needs only /bin/sh). The server bucket is public, so the download is
# unauthenticated: no HF credential is ever placed in the job environment (see `_derive_sandbox_token`).
#
# It fetches a public object and runs it as root, as PID 1, holding the sandbox token -- so it
# checks the bytes against `SANDBOX_SERVER_SHA256` and refuses to run them if they don't match.
# The check happens *before* `chmod +x`: an unverified file that is already executable is one
# slip away from being executed. Neither `sha256sum` nor `openssl` is guaranteed to exist in an
# arbitrary image, so both are tried; with neither, the default is to refuse rather than to run
# unverified code, and `SBX_ALLOW_UNVERIFIED_SERVER=1` (dedicated job `env=` or the image environment) is the way out for an
# image that has to. It never bypasses a *failed* check, only a missing tool.
_BOOTSTRAP_DOWNLOAD = """\
set -e
d=/tmp/.sbx-server
if command -v wget >/dev/null 2>&1; then wget -q -O "$d" "$SBX_SERVER_URL"
elif command -v curl >/dev/null 2>&1; then curl -fsSL -o "$d" "$SBX_SERVER_URL"
else cp "$SBX_SERVER_MOUNT/sbx-server-$SBX_SERVER_SHA256" "$d"; fi
if command -v sha256sum >/dev/null 2>&1; then
  actual=$(sha256sum "$d" | cut -d' ' -f1)
elif command -v openssl >/dev/null 2>&1; then
  actual=$(openssl dgst -sha256 -r "$d" | cut -d' ' -f1)
elif [ "${SBX_ALLOW_UNVERIFIED_SERVER:-}" = 1 ]; then
  echo "sbx: SBX_ALLOW_UNVERIFIED_SERVER=1: running the sandbox server unverified" >&2
  actual=$SBX_SERVER_SHA256  # opted out, so there is nothing left to compare against
else
  echo "sbx: cannot verify the sandbox server digest: this image has neither sha256sum nor" >&2
  echo "sbx: openssl, so refusing to run it. Use an image with either hash tool installed." >&2
  echo "sbx: Dedicated jobs may opt out with env={'SBX_ALLOW_UNVERIFIED_SERVER': '1'};" >&2
  echo "sbx: pool hosts require setting it in the image environment instead." >&2
  exit 1
fi
if [ "$actual" != "$SBX_SERVER_SHA256" ]; then
  echo "sbx: sandbox server digest mismatch: got $actual," >&2
  echo "sbx: expected $SBX_SERVER_SHA256." >&2
  echo "sbx: refusing to run it. Upgrade huggingface_hub if this client is pinned to a" >&2
  echo "sbx: digest that is no longer published." >&2
  exit 1
fi
chmod +x "$d"
unset SBX_SERVER_URL SBX_SERVER_MOUNT SBX_SERVER_SHA256 SBX_ALLOW_UNVERIFIED_SERVER
exec "$d"
"""


def _derive_sandbox_token(hf_token: str, nonce: str) -> str:
    """Derive the sandbox auth token from the user's HF token and the job's nonce.

    Stateless: any machine holding the same HF token can recompute it from the
    nonce stored in the job's labels, so `Sandbox.connect(job_id)` needs no local state.
    Only the derived token is passed to the sandbox server as a job secret; the HF token
    itself is not.

    Scope: one nonce is minted per *job*, not per sandbox. For a dedicated sandbox those are
    the same thing. In a pool this derives the *host* credential, which manages the pool and
    can recover per-sandbox tokens -- a leak there is host-wide. Each pooled sandbox also has
    its own random capability token, minted by the host server, which is what per-sandbox
    operations and `proxy_headers` use.

    This is not a hardened boundary in either direction. An untrusted image can own the
    sandbox port, so don't treat it as a guarantee that credentials stay out of the sandbox;
    and the token is derived for whichever job carries the matching nonce label, so don't
    treat holding it as proof of which job you are talking to.
    """
    return hmac.new(hf_token.encode(), f"hf-sandbox:{nonce}".encode(), hashlib.sha256).hexdigest()


def _duration_to_secs(duration: int | float | str) -> int:
    """Parse a duration like 300, "300s", "10m", "2h", "1d" into seconds."""
    if isinstance(duration, (int, float)):
        return int(duration)
    return parse_duration(duration)


@dataclass
class SandboxCommandResult:
    """Result of a command executed in a sandbox with [`Sandbox.run`]."""

    exit_code: int | None
    stdout: str
    stderr: str
    signal: int | None = None
    timed_out: bool = False
    duration_ms: int = 0

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    def __repr__(self) -> str:
        out = self.stdout if len(self.stdout) <= 80 else self.stdout[:77] + "..."
        return f"SandboxCommandResult(exit_code={self.exit_code}, stdout={out!r}, duration_ms={self.duration_ms})"


@dataclass
class SandboxProcess:
    """A background process started in a sandbox with [`Sandbox.run`]`(..., background=True)`.

    List a sandbox's processes with [`Sandbox.processes`] and stop one with [`SandboxProcess.kill`].
    Recently completed processes stay in the listing (the server keeps a bounded number of
    them), so `running` and `exit_code` tell whether a process is still alive or already
    exited (as of when it was listed).
    """

    # Server-assigned handle, and the only identifier that addresses this process.
    # `pid` is the OS pid: useful for correlating with `ps` inside the sandbox, but
    # the OS may reuse it, so it is observational only. `None` for a process on a
    # host running a server that predates opaque ids.
    id: str | None
    pid: int
    cmd: str | List[str]
    # Back-reference to the sandbox, used by `kill()`. Excluded from repr/eq so a process
    # stays a plain data object.
    _sandbox: "Sandbox" = field(repr=False, compare=False)
    tag: str | None = None
    started_at_ms: int | None = None
    running: bool = True
    exit_code: int | None = None

    def kill(self) -> bool:
        """Terminate the background process. Idempotent.

        Returns whether this call is what stopped it: `False` means it had already
        exited or been terminated, which is not an error.

        Note that a descendant which detaches with `setsid()` leaves the signalled
        process group and outlives this call. Delete the sandbox to be certain
        everything it started is gone.
        """
        if self.id is None:
            raise SandboxError(
                "This process cannot be stopped individually: its sandbox runs a sandbox server "
                "that predates opaque process ids, so the server never issued one. Recycle the "
                "pool's hosts, or delete the sandbox to stop everything it started."
            )
        response = self._sandbox._request("DELETE", f"/processes/{self.id}")
        return bool(response.json().get("killed", False))


@dataclass
class FileEntry:
    """A file or directory inside a sandbox."""

    name: str
    path: str
    type: Literal["file", "dir", "symlink"]
    size: int
    mtime_ms: int | None = None
    mode: str = ""


@contextmanager
def _open_download_target(local_path: Path) -> Iterator[BinaryIO]:
    """Yield a writer whose bytes only appear at `local_path` once the download completes.

    Writing straight to the destination has three problems: an interrupted transfer leaves a
    truncated file at the final name; `open(path, "wb")` follows a symlink sitting there, so
    the sandbox's output can be redirected into any file this process can write; and a
    predictable temp name is a race another local process can win.

    So the bytes go to a temp file in the destination directory, created `O_EXCL` (nobody
    else's file) and `O_NOFOLLOW` (not a symlink) with mode `0600` and an unguessable name,
    then `os.replace`d into place. `rename` does not follow symlinks either, so a symlinked
    destination is *replaced* rather than written through, and a failure leaves nothing behind.
    """
    tmp = local_path.parent / f".{local_path.name}.{token_hex(8)}.part"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(tmp, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            yield f
        os.replace(tmp, local_path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


class SandboxFiles:
    """Filesystem operations inside a sandbox, available as [`Sandbox.files`].

    In shared (pool) mode, paths are rooted at the sandbox's private home — the
    only place its code can write — so a leading `/` is taken relative to that
    home. In dedicated mode, paths are absolute on the container filesystem.

    Paths are normalized lexically, then resolved by the host server relative to an open
    home-directory descriptor. The file API refuses symlinks on every component, including
    links whose targets are inside the same home.
    """

    # Above this size, transfers are split into ranged requests over parallel
    # connections: a single TCP stream through the jobs proxy is limited by the
    # bandwidth-delay product (~2 MiB/s at ~100ms RTT); parallel streams scale it.
    PARALLEL_THRESHOLD = 2 * 1024 * 1024
    PARALLEL_CHUNK_SIZE = 1 * 1024 * 1024
    PARALLEL_MAX_WORKERS = 16
    # Ceiling on what `read`/`read_text` will materialize in memory. A parallel
    # read used to collect every chunk into a list and *then* join it, so a 2 GB
    # file peaked at roughly twice its size before the caller saw a byte. Reading
    # a file into memory is inherently bounded by the file; this makes the bound
    # explicit and points at the streaming alternative instead of dying in an
    # allocator.
    MAX_READ_BYTES = 512 * 1024 * 1024

    def __init__(self, sandbox: "Sandbox") -> None:
        self._sandbox = sandbox

    def read(self, path: str) -> bytes:
        """Read a file from the sandbox and return its content as bytes.

        Raises [`SandboxError`] above `MAX_READ_BYTES`; use [`download`] for
        anything that large, which streams to disk instead of buffering.
        """
        size = self.stat(path).size
        if size > self.MAX_READ_BYTES:
            raise SandboxError(
                f"{path} is {size} bytes, over the {self.MAX_READ_BYTES}-byte limit for reading into "
                "memory. Use `files.download(path, local_path)`, which streams to disk."
            )
        if size > self.PARALLEL_THRESHOLD:
            # Write each range into one preallocated buffer as it arrives, rather
            # than collecting every chunk and joining: the join doubled peak memory.
            buffer = bytearray(size)
            for offset, part in self._read_ranges(path, size):
                buffer[offset : offset + len(part)] = part
            return bytes(buffer)
        return self._read_range(path, 0, size)

    def read_text(self, path: str, encoding: str = "utf-8") -> str:
        """Read a file from the sandbox and return its content as a string."""
        return self.read(path).decode(encoding)

    def write(self, path: str, data: str | bytes | BinaryIO, mode: str | None = None) -> None:
        """Write content to a file in the sandbox (parent directories are created)."""
        if isinstance(data, str):
            data = data.encode()
        elif not isinstance(data, bytes):
            data = data.read()  # binary file object
        if len(data) > self.PARALLEL_THRESHOLD:
            self._write_ranges(path, data, mode)
            return
        params = {"path": path}
        if mode is not None:
            params["mode"] = mode
        self._sandbox._request("PUT", "/files/write", params=params, content=data)

    def upload(self, local_path: str | Path, path: str, mode: str | None = None) -> None:
        """Upload a local file to the sandbox.

        The file is opened once and both sized (`fstat`) and read through that same
        descriptor, so what is uploaded is the file that was measured -- rather than
