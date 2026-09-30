"""Tests for ledger/local_disk_blob_store.py (D-0013): layout, write-once, atomic convergence."""
import stat
import threading

import pytest

from nacre.core.blob_store import BlobStore
from nacre.ledger.local_disk_blob_store import LocalDiskBlobStore

REF = bytes(range(32))


def test_implements_the_interface(tmp_path):
    assert isinstance(LocalDiskBlobStore(tmp_path), BlobStore)


def test_layout_and_permissions(tmp_path):
    store = LocalDiskBlobStore(tmp_path / "blobs")
    assert store.put_if_absent(REF, b"cipher")
    path = tmp_path / "blobs" / REF.hex()[:2] / REF.hex()
    assert path.read_bytes() == b"cipher" and stat.S_IMODE(path.stat().st_mode) == 0o600


def test_write_once(tmp_path):
    store = LocalDiskBlobStore(tmp_path)
    assert store.put_if_absent(REF, b"first") is True
    assert store.put_if_absent(REF, b"second") is False
    assert store.get(REF) == b"first"


def test_concurrent_writers_converge_on_one_blob(tmp_path):
    store, results = LocalDiskBlobStore(tmp_path), []
    threads = [threading.Thread(target=lambda i=i: results.append(store.put_if_absent(REF, f"v{i}".encode())))
               for i in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(True) == 1 and store.get(REF) in {f"v{i}".encode() for i in range(16)}
    assert not list(tmp_path.rglob("*.tmp"))


def test_missing_and_bad_refs(tmp_path):
    store = LocalDiskBlobStore(tmp_path)
    with pytest.raises(KeyError):
        store.get(REF)
    assert store.exists(REF) is False
    with pytest.raises(ValueError):
        store.put_if_absent(b"short", b"x")
