"""
Functionality: The interface all root-key access goes through (interface only, no logic).
Owns: the RootKeyProvider protocol, the WrappedKey value type, and their contract.
Public entry: RootKeyProvider, WrappedKey
Decisions: D-0004
Assumptions: A-0008
Notes: D-0004 amendment 4: the root key wraps stream master keys and is reachable only through
  this interface, so a cloud key service can replace the local file (keys/local_file_root_key.py).
  Callers never see root-key bytes; they hand over a master key and get back a WrappedKey.
  `context` is bound into the wrapping as associated data (callers pass the stream_id bytes), so a
  wrapped master key copied onto another stream's row fails to unwrap.
  Rotation = rewrap (keys/rotate_root_key.py): a provider must still unwrap keys wrapped under
  older root-key versions it retains, while wrap() always uses the current version.
"""
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class WrappedKey:
    root_key_version: str
    wrapped: bytes


@runtime_checkable
class RootKeyProvider(Protocol):
    def current_version(self) -> str:
        """The root-key version that wrap() uses now."""
        ...

    def wrap(self, master_key: bytes, context: bytes) -> WrappedKey:
        """Wrap a stream master key under the current root-key version, bound to `context`."""
        ...

    def unwrap(self, wrapped: WrappedKey, context: bytes) -> bytes:
        """Recover a master key. Raises KeyError if its root-key version is not held, and
        ValueError if authentication fails (tampered bytes or wrong context)."""
        ...
