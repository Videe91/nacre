# Third-party notices

## all-MiniLM-L6-v2 (local embedder, D-0024)
- **Model:** `sentence-transformers/all-MiniLM-L6-v2`, ONNX export (`onnx/model.onnx`) and `tokenizer.json`.
- **Version:** pinned at commit `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`.
- **Licence:** Apache License 2.0.
- **Authors:** the sentence-transformers team (Nils Reimers et al.). Fine-tuned from
  `nreimers/MiniLM-L6-H384-uncased` (Microsoft MiniLM).
- **Not in this repository.**
  - `scripts/fetch_embedder.py` fetches the files from the pinned commit, or from our backup release asset
    <https://github.com/Videe91/nacre/releases/tag/embedder-minilm-l6-v2-1110a243> (unmodified copies, with the model
    card and a NOTICE).
  - It verifies the sha256s pinned in `src/nacre/recall/embed_local.py`:
    - `model.onnx`: `6fd5d72fe4589f189f8ebc006442dbb529bb7ce38f8082112682524616046452`
    - `tokenizer.json`: `be50c3628f2bf5bb5e3a7f17b1f74611b2561a3a27eeab05e5aa30f411572037`

## DejaVu fonts (test data, I1 OCR set, D-0027)
`tests/ledger/secret_corpus/fonts/`: DejaVu 2.37, Bitstream Vera licence plus Arev (`LICENSE-DejaVu.txt`).

## Secret-corpus negatives (test data)
CPython standard-library files (PSF licence) and permissively licensed third-party package files. Each set's
`MANIFEST.json` and `LICENSES/` folder give per-file origin and licence.
