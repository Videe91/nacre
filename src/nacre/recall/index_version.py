"""
Functionality: Write one encrypted recall-index entry for an interpretation version, in the version's transaction.
Owns: the entry plaintext format (deterministic CBOR: embedding bytes, index text, addresses, kind), which text is
  indexed per kind, the entry AAD, sealing under the version key's recall_index sub-key, the generation bookkeeping
  (first generation created on first use; the active generation's embedder must be the caller's), and decoding an
  entry back (for the cache, R10).
Public entry: index_version(), open_entry(), entry_aad(), IndexEntry, IndexEmbedderMismatch, default_embedder()
Decisions: D-0024, D-0023, D-0008
Assumptions: A-0034, A-0031
Notes: Called by stores/write_version.py right after the version's projection rows, so an entry exists if and only
  if the version does (D-0024 §2). Every version is indexed, whatever its status: recall reads status from the
  projection at its snapshot, never from the entry.
  - Key: the version event's own data key, which is its contributor-set key (D-0023), through the RECALL_INDEX
    sealing sub-key. Destroying that key makes the entry unreadable; no new erasure path.
  - Columns hold only ids and ciphertext (0012 migration); the embedding, text and addresses are inside the
    ciphertext. Embedding = 384 little-endian float32 bytes (CBOR has no floats here, D-0008).
  - Index text (D1): a belief or fallback indexes its `nucleus` (the retrieval handle, D-0017), falling back to
    `support_text`; an episode has no text and indexes "" (it ranks through identity addresses only).
  - Addresses: `content["addresses"]` when present (D-0025 §3, the D-0018 amendment is not built yet), else [].
  - A stream whose active index generation has a different embedder refuses the write (IndexEmbedderMismatch):
    a model change needs a full re-index into a new generation first (owner decision, D-0024).
"""
from dataclasses import dataclass
from functools import lru_cache
from uuid import UUID

import numpy as np

from nacre.core.decode_cbor import decode_cbor
from nacre.core.embedder import Embedder
from nacre.core.encode_cbor import encode_cbor
from nacre.core.root_key_provider import RootKeyProvider
from nacre.keys.decrypt_payload import DecryptError, check_header, open_bytes
from nacre.keys.encrypt_payload import SubkeyPurpose, derive_subkey, seal_bytes
from nacre.keys.get_or_create_key import DataKey, load_key
from nacre.scopes.open_scoped_session import ScopedSession

ENTRY_VERSION = 1
_AAD_LABEL = b"nacre-recall-index-v1|"


class IndexEmbedderMismatch(RuntimeError):
    """The stream's active index generation was built with another embedder; re-index before writing."""


@dataclass(frozen=True)
class IndexEntry:
    version_event_id: UUID
    key_id: UUID
    kind: str
    text: str
    addresses: tuple[str, ...]
    embedding: np.ndarray


@lru_cache(maxsize=1)
def default_embedder() -> Embedder:
    from nacre.recall.embed_local import LocalEmbedder
    return LocalEmbedder()


def entry_aad(stream_id: UUID, generation: int, version_event_id: UUID, embedder_id: str) -> bytes:
    return _AAD_LABEL + encode_cbor([str(stream_id), generation, str(version_event_id), embedder_id])


def _index_text(kind: str, content: dict) -> str:
    if kind == "episode":
        return ""
    return content.get("nucleus") or content.get("support_text") or ""


def _generation(session: ScopedSession, stream_id: UUID, embedder_id: str) -> int:
    conn = session.conn
    gen = conn.execute("SELECT recall.active_generation(%s)", (stream_id,)).fetchone()[0]
    conn.execute("INSERT INTO recall.index_generations (stream_id, generation, embedder_id) VALUES (%s, %s, %s) "
                 "ON CONFLICT (stream_id, generation) DO NOTHING", (stream_id, gen, embedder_id))
    have = conn.execute("SELECT embedder_id FROM recall.index_generations WHERE stream_id = %s AND generation = %s",
                        (stream_id, gen)).fetchone()[0]
    if have != embedder_id:
        raise IndexEmbedderMismatch(f"stream {stream_id} index generation {gen} uses {have}; re-index first")
    return gen


def index_version(session: ScopedSession, key_provider: RootKeyProvider, stream_id: UUID, version_event_id: UUID,
                  key_id: UUID, kind: str, content: dict, embedder: Embedder | None = None) -> None:
    """Seal and insert the index entry of one version event (same transaction as the version)."""
    embedder = embedder or default_embedder()
    key: DataKey | None = load_key(session.conn, key_provider, key_id)
    if key is None:
        raise ValueError(f"the version's key {key_id} is not loadable here")
    text = _index_text(kind, content)
    addresses = [str(a) for a in content.get("addresses", [])]
    vec = np.ascontiguousarray(embedder.embed([text])[0], dtype="<f4")
    plain = encode_cbor({"v": ENTRY_VERSION, "embedding": vec.tobytes(), "text": text, "addresses": addresses,
                         "kind": kind})
    gen = _generation(session, stream_id, embedder.embedder_id)
    body = seal_bytes(session.conn, key, entry_aad(stream_id, gen, version_event_id, embedder.embedder_id), plain,
                      subkey=SubkeyPurpose.RECALL_INDEX)
    session.conn.execute("INSERT INTO recall.index_entries (stream_id, index_generation, version_event_id, key_id, "
                         "embedder_id, body) VALUES (%s, %s, %s, %s, %s, %s)",
                         (stream_id, gen, version_event_id, key_id, embedder.embedder_id, body))


def open_entry(key: DataKey, stream_id: UUID, generation: int, version_event_id: UUID, embedder_id: str,
               body: bytes, dim: int) -> IndexEntry:
    """Decrypt and decode one entry (raises on a wrong key, a tampered body or a malformed plaintext)."""
    if check_header(body) != key.key_id:
        raise DecryptError(f"index entry {version_event_id}: header names another key")
    plain = decode_cbor(open_bytes(derive_subkey(key.material, SubkeyPurpose.RECALL_INDEX),
                                   entry_aad(stream_id, generation, version_event_id, embedder_id), body))
    if plain.get("v") != ENTRY_VERSION or len(plain["embedding"]) != 4 * dim:
        raise ValueError(f"index entry {version_event_id}: unknown format or wrong dimension")
    return IndexEntry(version_event_id, key.key_id, plain["kind"], plain["text"], tuple(plain["addresses"]),
                      np.frombuffer(plain["embedding"], dtype="<f4").astype(np.float32))
