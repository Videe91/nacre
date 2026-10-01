"""Research benchmark (not product code): encrypted embedding storage options for the Phase 3 ADR.

For N vectors of D=384 float32 (or float16), grouped into encrypted blobs of R vectors each
(R=1 means one encrypted row per record), measure:
  fetch   - SELECT the scope's blobs from Postgres (bytea), one persistent psycopg connection (NOT core.db.open_pool), warm server cache
  decrypt - AES-256-GCM decrypt of every blob
  load    - np.frombuffer + concatenate into one matrix
  search  - normalised dot-product top-k (k=50) over the matrix (brute force)
cold = fetch + decrypt + load + search; warm = search only (decrypted matrix cached in memory).
Uses a throwaway database on the local dev container; dropped at the end.
Evidence: docs/assumptions/evidence/A-0032-encrypted-recall-index-2026-10-01.md (D-0024, proposed).
Needs numpy, which is NOT a Nacre dependency: run it from a separate venv (numpy, cryptography, psycopg[binary]):

    <bench-venv>/bin/python scripts/bench_encrypted_vectors.py > out.jsonl
"""
import json, os, statistics, sys, time, uuid

import numpy as np
import psycopg
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

DSN_ADMIN = "host=127.0.0.1 port=54329 user=postgres password=nacre_dev dbname=postgres"
D = 384
K = 50
REPS = 15


def pctl(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))]


def summarise(xs):
    return {"median_ms": round(statistics.median(xs) * 1000, 2), "p95_ms": round(pctl(xs, 0.95) * 1000, 2)}


def run_case(conn, n, r, dtype, rng):
    vecs = rng.standard_normal((n, D)).astype(np.float32)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
    vecs = vecs.astype(dtype)
    n_blobs = (n + r - 1) // r
    keys = [AESGCM.generate_key(bit_length=256) for _ in range(min(n_blobs, 2000))]  # key reuse beyond 2000 is irrelevant to timing
    with conn.cursor() as cur:
        cur.execute("TRUNCATE bench_blobs")
        rows = []
        for b in range(n_blobs):
            chunk = vecs[b * r:(b + 1) * r]
            key = keys[b % len(keys)]
            nonce = os.urandom(12)
            ct = AESGCM(key).encrypt(nonce, chunk.tobytes(), b"scope-1")
            rows.append((b, b % len(keys), nonce + ct, chunk.shape[0]))
        with cur.copy("COPY bench_blobs (blob_id, key_idx, body, n_vec) FROM STDIN") as cp:
            for row in rows:
                cp.write_row(row)
    conn.commit()
    stored_bytes = sum(len(x[2]) for x in rows)
    q = rng.standard_normal(D).astype(np.float32)
    q /= np.linalg.norm(q)
    q = q.astype(dtype)

    t_fetch, t_dec, t_load, t_search, t_cold = [], [], [], [], []
    for _ in range(REPS):
        t0 = time.perf_counter()
        with conn.cursor() as cur:
            cur.execute("SELECT key_idx, body FROM bench_blobs WHERE scope = 1 ORDER BY blob_id")
            fetched = cur.fetchall()
        t1 = time.perf_counter()
        plains = [AESGCM(keys[k]).decrypt(bytes(body[:12]), bytes(body[12:]), b"scope-1") for k, body in fetched]
        t2 = time.perf_counter()
        mat = np.frombuffer(b"".join(plains), dtype=dtype).reshape(-1, D)
        t3 = time.perf_counter()
        scores = mat @ q
        top = np.argpartition(-scores, K)[:K]
        top = top[np.argsort(-scores[top])]
        t4 = time.perf_counter()
        t_fetch.append(t1 - t0); t_dec.append(t2 - t1); t_load.append(t3 - t2); t_search.append(t4 - t3); t_cold.append(t4 - t0)
    # correctness: encrypted path returns the same top-k as plaintext
    ref = vecs @ q
    ref_top = np.argpartition(-ref, K)[:K]
    assert set(ref_top.tolist()) == set(top.tolist()), "top-k mismatch"
    # warm: matrix already decrypted and cached; search only
    warm = []
    for _ in range(200):
        t0 = time.perf_counter()
        scores = mat @ q
        top = np.argpartition(-scores, K)[:K]
        top = top[np.argsort(-scores[top])]
        warm.append(time.perf_counter() - t0)
    return {
        "n": n, "vectors_per_blob": r, "dtype": np.dtype(dtype).name, "blobs": n_blobs,
        "stored_mb": round(stored_bytes / 1e6, 2),
        "fetch": summarise(t_fetch), "decrypt": summarise(t_dec), "load": summarise(t_load),
        "search_cold": summarise(t_search), "cold_total": summarise(t_cold), "warm_search": summarise(warm),
        "matrix_mb_in_memory": round(mat.nbytes / 1e6, 2),
    }


def main():
    db = "nacre_bench_" + uuid.uuid4().hex[:8]
    with psycopg.connect(DSN_ADMIN, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{db}"')
    try:
        with psycopg.connect(DSN_ADMIN.replace("dbname=postgres", f"dbname={db}")) as conn:
            conn.execute("CREATE TABLE bench_blobs (scope int NOT NULL DEFAULT 1, blob_id int, key_idx int, body bytea, n_vec int)")
            conn.execute("ALTER TABLE bench_blobs ALTER COLUMN body SET STORAGE EXTERNAL")  # encrypted: no point compressing
            conn.commit()
            rng = np.random.default_rng(20261001)
            results = []
            cases = [(1_000, 1), (1_000, 64), (10_000, 1), (10_000, 64), (100_000, 64), (100_000, 1024), (100_000, 1)]
            for n, r in cases:
                for dtype in (np.float32, np.float16):
                    if r == 1 and dtype == np.float16:
                        continue
                    res = run_case(conn, n, r, dtype, rng)
                    print(json.dumps(res), flush=True)
                    results.append(res)
    finally:
        with psycopg.connect(DSN_ADMIN, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{db}"')
    print(json.dumps({"python": sys.version.split()[0], "numpy": np.__version__, "reps": REPS, "k": K, "d": D}))


if __name__ == "__main__":
    main()
