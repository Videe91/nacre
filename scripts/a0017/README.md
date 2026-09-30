# A-0017 evaluation scripts (not product code)

Evaluation of gitleaks v8.30.1 rules under Python `re`, `google-re2`, and Go `regexp` (gitleaks' own
engine, used as ground truth). Report: `docs/assumptions/evidence/A-0017-regex-engines.md`.

Needs a separate environment (these are NOT project dependencies) and Go:

    python3.14 -m venv /tmp/re2env && /tmp/re2env/bin/pip install google-re2==1.1.20251105 hypothesis
    curl -sL -o gitleaks-v8.30.1.toml https://raw.githubusercontent.com/gitleaks/gitleaks/v8.30.1/config/gitleaks.toml

Run from the repo root (they read the toml from the current directory; corpus = `.venv` site-packages):

| Script | Produces |
|---|---|
| `eval_regex_engines.py <toml> <corpus_dir> <out.json>` | compile / span agreement re vs re2 / per-probe worst-case timing |
| `summarize.py <out.json>` | text summary |
| `scaling.py` | growth of search time with input length for the slowest rules |
| `export_and_compare.py` | Go ground truth vs re2 and re(ASCII) over corpus + generated positives (uses `go_spans/`) |
| `supplement.py` | same for the 22 rules Python `re` cannot compile (positives from a generation-only rewrite) |
