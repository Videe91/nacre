"""Tests for keys/local_file_root_key.py (D-0004 amendments 4, 6)."""
import os
import re
import secrets
import stat

import pytest

from nacre.core.root_key_provider import RootKeyProvider, WrappedKey
from nacre.keys.local_file_root_key import LocalFileRootKeyProvider

CTX_A, CTX_B = b"\x01" * 16, b"\x02" * 16


@pytest.fixture
def provider(tmp_path):
    return LocalFileRootKeyProvider.initialise(tmp_path / "rootkeys")


def test_implements_the_protocol(provider):
    assert isinstance(provider, RootKeyProvider)


def test_wrap_unwrap_round_trip(provider):
    mk = secrets.token_bytes(32)
    w = provider.wrap(mk, CTX_A)
    assert w.root_key_version == provider.current_version()
    assert mk not in w.wrapped
    assert provider.unwrap(w, CTX_A) == mk


def test_wrapping_is_randomised(provider):
    mk = secrets.token_bytes(32)
    assert provider.wrap(mk, CTX_A).wrapped != provider.wrap(mk, CTX_A).wrapped


def test_wrong_context_fails(provider):
    w = provider.wrap(secrets.token_bytes(32), CTX_A)
    with pytest.raises(ValueError, match="authentication"):
        provider.unwrap(w, CTX_B)


@pytest.mark.parametrize("position", [0, 12, -1])
def test_tampered_wrap_fails(provider, position):
    w = provider.wrap(secrets.token_bytes(32), CTX_A)
    b = bytearray(w.wrapped)
    b[position] ^= 0x01
    with pytest.raises(ValueError, match="authentication"):
        provider.unwrap(WrappedKey(w.root_key_version, bytes(b)), CTX_A)


def test_version_is_bound_into_the_wrap(provider):
    w = provider.wrap(secrets.token_bytes(32), CTX_A)
    new = provider.create_version()
    os.link(provider._key_path(w.root_key_version), provider._key_path("alias"))  # same key bytes, other name
    with pytest.raises(ValueError, match="authentication"):
        provider.unwrap(WrappedKey("alias", w.wrapped), CTX_A)
    assert new == provider.current_version()


def test_rotation_new_version_is_current_and_old_still_unwraps(provider):
    mk = secrets.token_bytes(32)
    old = provider.wrap(mk, CTX_A)
    new_version = provider.create_version()
    assert provider.current_version() == new_version != old.root_key_version
    assert provider.unwrap(old, CTX_A) == mk
    rewrapped = provider.wrap(provider.unwrap(old, CTX_A), CTX_A)
    assert rewrapped.root_key_version == new_version


def test_destroyed_version_can_never_unwrap_again(provider):
    mk = secrets.token_bytes(32)
    old = provider.wrap(mk, CTX_A)
    key_file = provider._key_path(old.root_key_version)
    provider.create_version()
    provider.destroy_version(old.root_key_version)
    assert not key_file.exists()
    with pytest.raises(KeyError, match="not held"):
        provider.unwrap(old, CTX_A)


def test_current_version_cannot_be_destroyed(provider):
    with pytest.raises(ValueError, match="current"):
        provider.destroy_version(provider.current_version())


def test_files_are_owner_only(provider):
    for f in provider._dir.iterdir():
        assert stat.S_IMODE(f.stat().st_mode) == 0o600, f.name


def test_refuses_a_group_readable_key(provider):
    provider._key_path(provider.current_version()).chmod(0o640)
    with pytest.raises(PermissionError, match="chmod 600"):
        provider.wrap(secrets.token_bytes(32), CTX_A)


def test_version_ids_fit_the_database_column(provider):
    assert re.fullmatch(r"[A-Za-z0-9._:-]{1,64}", provider.create_version())


@pytest.mark.parametrize("bad", [b"short", secrets.token_bytes(33), bytearray(32)], ids=["short", "long", "bytearray"])
def test_master_keys_must_be_32_bytes(provider, bad):
    with pytest.raises(ValueError, match="32 bytes"):
        provider.wrap(bad, CTX_A)


@pytest.mark.parametrize("version", ["../etc/passwd", "", ".hidden"])
def test_path_traversal_versions_are_rejected(provider, version):
    with pytest.raises(KeyError):
        provider.unwrap(WrappedKey(version, b"x" * 60), CTX_A)


def test_opening_an_uninitialised_directory_fails(tmp_path):
    with pytest.raises(FileNotFoundError):
        LocalFileRootKeyProvider(tmp_path)


def test_reopening_uses_the_same_keys(provider):
    mk = secrets.token_bytes(32)
    w = provider.wrap(mk, CTX_A)
    assert LocalFileRootKeyProvider(provider._dir).unwrap(w, CTX_A) == mk
