"""
Functionality: Embed texts locally with the pinned all-MiniLM-L6-v2 ONNX model, end to end.
Owns: the pins (revision, file sha256s), verifying the model files before loading, tokenisation (truncation at 256
  tokens, padding), ONNX inference on CPU, mean pooling over the attention mask, and L2 normalisation.
Public entry: LocalEmbedder, EmbedderPinError, EMBEDDER_ID, default_model_dir()
Decisions: D-0024
Assumptions: A-0034
Notes: The only file that imports onnxruntime or tokenizers (checked by scripts/check_structure.py).
  - Model: sentence-transformers/all-MiniLM-L6-v2, Apache-2.0 (sentence-transformers team, Nils Reimers et al.;
    fine-tuned from nreimers/MiniLM-L6-H384-uncased, Microsoft MiniLM). Attribution: THIRD_PARTY_NOTICES.md.
  - The files are NOT in git (owner, 2026-10-01). scripts/fetch_embedder.py fetches them from the pinned commit
    (never a branch), falling back to our backup release asset, and verifies the sha256s. This file never touches
    the network: it loads from a local directory and refuses to start if a file is missing or its sha256 differs.
  - Equivalence (owner decision): output is compared with frozen sentence-transformers reference vectors by cosine
    >= 0.9999 per text (tests/recall/data/minilm_reference_vectors.json), never by byte equality.
  - onnxruntime telemetry is disabled before the session is created (2026-10-02: the R5 build saw signs that
    onnxruntime's native telemetry client may use the network on macOS; native code is not stopped by Python socket
    blocks). Whether that is enough is an open owner question; see CURRENT.md.
  - Pipeline as sentence-transformers' modules.json: Transformer (max_seq_length 256) -> mean pooling -> Normalize.
"""
import hashlib
import os
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
_PINNED_SHA256 = (   # in FILE_NAMES order; kept apart from the names so no file name sits beside a digest
    "6fd5d72fe4589f189f8ebc006442dbb529bb7ce38f8082112682524616046452",
    "be50c3628f2bf5bb5e3a7f17b1f74611b2561a3a27eeab05e5aa30f411572037",
)
FILE_NAMES = ("model.onnx", "tokenizer.json")
FILES = dict(zip(FILE_NAMES, _PINNED_SHA256))
EMBEDDER_ID = f"{MODEL_NAME}@{REVISION}#{FILES['model.onnx']}"
DIM = 384
MAX_SEQ_LEN = 256
BATCH = 64


class EmbedderPinError(RuntimeError):
    """A model file is missing or does not match its pinned sha256."""


def default_model_dir() -> Path:
    env = os.environ.get("NACRE_EMBEDDER_DIR")
    return Path(env) if env else Path.home() / ".cache" / "nacre" / "embedders" / f"all-MiniLM-L6-v2-{REVISION[:8]}"


def _verified(model_dir: Path) -> dict[str, bytes]:
    out = {}
    for name, want in FILES.items():
        path = model_dir / name
        if not path.is_file():
            raise EmbedderPinError(f"{name} missing in {model_dir}; run scripts/fetch_embedder.py")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != want:
            raise EmbedderPinError(f"{name} in {model_dir} does not match its pinned sha256; refusing to load")
        out[name] = data
    return out


class LocalEmbedder:
    embedder_id = EMBEDDER_ID
    dim = DIM

    def __init__(self, model_dir: Path | None = None, *, threads: int = 0):
        files = _verified(Path(model_dir) if model_dir else default_model_dir())
        self._tok = Tokenizer.from_str(files["tokenizer.json"].decode())
        self._tok.enable_truncation(max_length=MAX_SEQ_LEN)
        self._tok.enable_padding(pad_id=0, pad_token="[PAD]")
        ort.disable_telemetry_events()          # D-0024 "no network at runtime": opt out of onnxruntime telemetry
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = threads
        opts.log_severity_level = 3
        self._sess = ort.InferenceSession(files["model.onnx"], sess_options=opts, providers=["CPUExecutionProvider"])
        self._inputs = {i.name for i in self._sess.get_inputs()}

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        texts = list(texts)
        if not all(isinstance(t, str) for t in texts):
            raise TypeError("embed() takes a sequence of str")
        out = np.empty((len(texts), DIM), dtype=np.float32)
        for start in range(0, len(texts), BATCH):
            out[start:start + BATCH] = self._embed_batch(texts[start:start + BATCH])
        return out

    def _embed_batch(self, texts: list[str]) -> np.ndarray:
        enc = self._tok.encode_batch(texts)
        ids = np.array([e.ids for e in enc], dtype=np.int64)
        mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
        feed = {"input_ids": ids, "attention_mask": mask, "token_type_ids": np.zeros_like(ids)}
        hidden = self._sess.run(None, {k: v for k, v in feed.items() if k in self._inputs})[0]
        m = mask[..., None].astype(np.float32)
        pooled = (hidden * m).sum(axis=1) / np.clip(m.sum(axis=1), 1e-9, None)
        return (pooled / np.clip(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-12, None)).astype(np.float32)
