# Module rules: one file = one functionality, end to end

## The rule
Each functionality lives in exactly one file that does the whole job: input validation,
the logic, the storage calls, and the errors. Someone reading that one file understands
the whole feature.

Good:  `src/nacre/ledger/append_event.py`   → appends one event, fully
Bad:   `src/nacre/ledger.py` doing append + read + verify + shred (a god-file)
Bad:   append logic split across `validators.py`, `helpers.py`, `utils.py`

## Layout
- One folder per capability (ledger, scopes, capture, gate, sleep, stores, recall, …).
- Inside it, one file per functionality, named as a verb phrase: `append_event.py`, `verify_chain.py`.
- One matching test file per functionality: `tests/ledger/test_append_event.py`.
- Truly shared building blocks (e.g. the database connection, the event data type) live in
  `src/nacre/core/` and must be tiny and boring. No `utils.py`, no `helpers.py`.

## Required file header (checked by scripts/check_structure.py)
```python
"""
Functionality: Append one event to the ledger, end to end.
Owns: validation, secret stripping, sequencing, sealing, atomic write.
Public entry: append_event()
Decisions: D-0002, D-0004
Assumptions: A-0001
Notes: (D1 local design choices go here)
"""
```

## Limits
- Soft limit 300 lines, hard limit 400 lines per file. Over the limit → split into two
  functionalities (and two files), not into helpers.
- A file may import from `core/` and call other functionalities' **public entry** only.
- Every new file is registered in `docs/modules/INDEX.md` in the same commit.
