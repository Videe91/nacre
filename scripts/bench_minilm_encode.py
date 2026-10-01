"""Research benchmark (not product code): local MiniLM query-encode latency (CPU) for the Phase 3 ADR.
Evidence: docs/assumptions/evidence/A-0032-encrypted-recall-index-2026-10-01.md (D-0024, proposed).
Needs sentence-transformers + torch (NOT Nacre dependencies) and the model already in the HF cache; run offline:

    HF_HUB_OFFLINE=1 <venv-with-sentence-transformers>/bin/python scripts/bench_minilm_encode.py
"""
import json, statistics, time, sys
import numpy as np, torch
from sentence_transformers import SentenceTransformer
torch.set_num_threads(4)
t0 = time.perf_counter(); m = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2", device="cpu"); load = time.perf_counter() - t0
q = "Which retry policy should the payments client use after the March incident?"
for _ in range(5): m.encode([q])
xs = []
for _ in range(100):
    t = time.perf_counter(); m.encode([q], normalize_embeddings=True); xs.append(time.perf_counter() - t)
docs = [f"Lesson {i}: the deploy job for service-{i%37} must pin the base image digest before release." for i in range(1000)]
t = time.perf_counter(); E = m.encode(docs, batch_size=64, normalize_embeddings=True); batch = time.perf_counter() - t
xs.sort()
print(json.dumps({"model": "sentence-transformers/all-MiniLM-L6-v2", "device": "cpu", "threads": 4, "load_s": round(load, 2),
  "query_encode_median_ms": round(statistics.median(xs)*1000, 2), "query_encode_p95_ms": round(xs[94]*1000, 2),
  "batch_1000_docs_s": round(batch, 2), "dim": int(E.shape[1]), "dtype": str(E.dtype), "python": sys.version.split()[0],
  "sentence_transformers": __import__("sentence_transformers").__version__, "torch": torch.__version__}))
