"""
Research script (not product code): freeze reference vectors for the local embedder's equivalence test (D-0024
owner decision 4: the ONNX runtime output must match sentence-transformers within cosine >= 0.9999).
Run ONCE, in a venv that has sentence-transformers (not a Nacre dependency), offline from the HF cache at the pinned
revision:

    HF_HUB_OFFLINE=1 <venv-with-sentence-transformers>/bin/python scripts/make_embedder_reference.py

Writes tests/recall/data/minilm_reference_vectors.json and refuses to overwrite it.
"""
import json
import sys
from pathlib import Path

import sentence_transformers
import torch
from sentence_transformers import SentenceTransformer

REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
OUT = Path(__file__).resolve().parent.parent / "tests/recall/data/minilm_reference_vectors.json"
TEXTS = [
    "",
    "a",
    "The deploy job must pin the base image digest before release.",
    "Pin the base image by digest in the release pipeline.",
    "Which retry policy should the payments client use after the March incident?",
    "The payments client retries three times with exponential backoff capped at 30 seconds.",
    "The database pool for the session store is 267 connections at most.",
    "Café naïve résumé — déjà vu; Straße; ﬁ ligature; full-width ＡＢＣ１２３.",
    "日本語のテキストも埋め込めます。",
    "Ελληνικά και русский текст в одном предложении.",
    "emoji test 🚀🔥 and a zero-width​joiner",
    "def add(a, b):\n    return a + b\n",
    "SELECT key_id FROM keys.data_keys WHERE stream_id = $1 AND month = ANY($2);",
    "code:src/payments/retry.py system:payments entity:acme-corp",
    "   leading and trailing whitespace   ",
    "UPPER CASE SENTENCE WITH NUMBERS 12345 AND SYMBOLS !@#$%^&*()",
    "The quick brown fox jumps over the lazy dog.",
    "A fast auburn fox leaps above an idle hound.",
    "Never store plaintext embeddings in Postgres; WAL and backups keep them after erasure.",
    "Erasure must reach every derived copy of a person's words.",
    "word " * 400,                                        # well past the 256-token truncation
    "The canary step sends 5 percent of traffic for 10 minutes before promotion. " * 20,
    "\t\ttabs\tand\nnewlines\r\nmixed",
    "1234567890",
    "https://example.test/path?query=value&other=1#fragment",
]


def main():
    if OUT.exists():
        sys.exit(f"{OUT} exists: the reference is frozen; never regenerate")
    torch.set_num_threads(1)
    m = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2", revision=REVISION, device="cpu")
    vecs = m.encode(TEXTS, normalize_embeddings=True, batch_size=8, convert_to_numpy=True)
    OUT.write_text(json.dumps({
        "model": "sentence-transformers/all-MiniLM-L6-v2", "revision": REVISION, "device": "cpu",
        "sentence_transformers": sentence_transformers.__version__, "torch": torch.__version__,
        "normalize_embeddings": True, "max_seq_length": m.max_seq_length, "dim": int(vecs.shape[1]),
        "tolerance": "cosine >= 0.9999 per text (D-0024 owner decision 4)",
        "items": [{"text": t, "vector": [float(x) for x in v]} for t, v in zip(TEXTS, vecs)],
    }, ensure_ascii=False, indent=0) + "\n")
    print(f"wrote {len(TEXTS)} reference vectors, dim {vecs.shape[1]}, max_seq_length {m.max_seq_length}")


if __name__ == "__main__":
    main()
