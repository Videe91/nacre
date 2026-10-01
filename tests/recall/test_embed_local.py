"""Tests for recall/embed_local.py (D-0024): equivalence with frozen sentence-transformers vectors (cosine >= 0.9999),
pin refusal, no network, shape and norm; and scripts/fetch_embedder.py's verification (pinned bytes only).
The model files must be fetched first: .venv/bin/python scripts/fetch_embedder.py"""
import hashlib
import importlib.util
import json
import shutil
import socket
from pathlib import Path

import numpy as np
import pytest

from nacre.recall.embed_local import (DIM, EMBEDDER_ID, FILES, MAX_SEQ_LEN, EmbedderPinError, LocalEmbedder,
                                      default_model_dir)

ROOT = Path(__file__).resolve().parents[2]
REF = json.loads((ROOT / "tests/recall/data/minilm_reference_vectors.json").read_text())


@pytest.fixture(scope="module")
def emb():
    try:
        return LocalEmbedder()
    except EmbedderPinError as e:
        pytest.fail(f"{e} (the embedder gate needs the pinned files; never skipped)")


def test_onnx_output_matches_the_frozen_reference_vectors(emb):
    assert REF["revision"] in EMBEDDER_ID and REF["dim"] == DIM and REF["max_seq_length"] == MAX_SEQ_LEN
    got = emb.embed([i["text"] for i in REF["items"]])
    want = np.array([i["vector"] for i in REF["items"]], dtype=np.float32)
    cos = (got * want).sum(axis=1)
    assert cos.min() >= 0.9999, [(REF["items"][i]["text"][:30], float(c)) for i, c in enumerate(cos) if c < 0.9999]


def test_vectors_are_unit_float32_of_the_right_shape_and_deterministic(emb):
    v = emb.embed(["alpha", "beta gamma", ""])
    assert v.shape == (3, DIM) and v.dtype == np.float32
    assert np.allclose(np.linalg.norm(v, axis=1), 1.0, atol=1e-5)
    assert np.array_equal(v, emb.embed(["alpha", "beta gamma", ""]))
    assert emb.embed([]).shape == (0, DIM)


def test_batching_does_not_change_vectors(emb):
    texts = [f"lesson {i}: pin the image digest for service-{i % 7}" for i in range(150)]   # > one batch of 64
    together = emb.embed(texts)
    alone = np.vstack([emb.embed([t]) for t in texts[:5] + texts[-5:]])
    assert np.allclose(together[list(range(5)) + list(range(145, 150))], alone, atol=1e-5)


def test_long_texts_are_truncated_not_rejected(emb):
    long = "word " * 2000
    assert np.allclose(emb.embed([long]), emb.embed(["word " * 600]), atol=1e-5)   # both beyond 256 tokens


def test_non_str_input_is_refused(emb):
    with pytest.raises(TypeError):
        emb.embed([b"bytes"])


def test_embedder_id_fits_the_index_schema():
    import re
    assert re.fullmatch(r"[A-Za-z0-9._/@#:+-]{1,200}", EMBEDDER_ID)   # 0012_recall_index.sql CHECK
    assert EMBEDDER_ID.endswith("#" + FILES["model.onnx"])


@pytest.mark.parametrize("damage", ["tamper_model", "tamper_tokenizer", "missing"])
def test_a_missing_or_altered_file_refuses_to_load(tmp_path, damage):
    src = default_model_dir()
    for name in FILES:
        shutil.copy(src / name, tmp_path / name)
    if damage == "missing":
        (tmp_path / "tokenizer.json").unlink()
    else:
        f = tmp_path / ("model.onnx" if damage == "tamper_model" else "tokenizer.json")
        f.write_bytes(f.read_bytes() + b" ")
    with pytest.raises(EmbedderPinError):
        LocalEmbedder(tmp_path)


def test_loading_and_embedding_never_touch_the_network(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("network access attempted")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    LocalEmbedder().embed(["offline"])


# ---- scripts/fetch_embedder.py: only the pinned bytes are ever installed ----
_spec = importlib.util.spec_from_file_location("fetch_embedder", ROOT / "scripts/fetch_embedder.py")
fetch = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fetch)


def test_fetch_installs_only_matching_bytes_and_falls_through_a_bad_source(tmp_path):
    good, bad = tmp_path / "good", tmp_path / "bad"
    good.mkdir(), bad.mkdir()
    payload = b"pinned bytes"
    (good / "tokenizer.json").write_bytes(payload)
    (bad / "tokenizer.json").write_bytes(b"tampered")
    want = hashlib.sha256(payload).hexdigest()
    dest = tmp_path / "dest"
    dest.mkdir()
    srcs = [("release", bad.as_uri() + "/{path}"), ("release", good.as_uri() + "/{path}")]
    assert fetch.fetch_one("tokenizer.json", want, dest, srcs) == "release"
    assert (dest / "tokenizer.json").read_bytes() == payload


def test_fetch_installs_nothing_when_no_source_matches(tmp_path):
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "tokenizer.json").write_bytes(b"tampered")
    dest = tmp_path / "dest"
    dest.mkdir()
    with pytest.raises(SystemExit):
        fetch.fetch_one("tokenizer.json", "0" * 64, dest, [("release", bad.as_uri() + "/{path}")])
    assert not any(dest.iterdir())


def test_fetch_uses_the_pinned_commit_never_a_branch():
    assert fetch.REVISION in fetch.HF and "/resolve/main/" not in fetch.HF
    assert fetch.RELEASE.startswith("https://github.com/Videe91/nacre/releases/download/embedder-minilm-l6-v2-1110a243/")
