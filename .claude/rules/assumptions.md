# Assumption rules

An assumption is anything we act on but have not proven. Hidden assumptions are the #1 cause of
wasted builds, so they are always written.

- Every assumption gets an ID `A-NNNN` in `docs/assumptions/ASSUMPTIONS.md`.
- Each entry has: statement, why we believe it, **how we'll test it**, what breaks if it's wrong,
  status (`open` / `validated` / `invalidated`), and evidence link when resolved.
- Code that relies on an assumption lists its ID in the file header.
- When an assumption is **invalidated**: mark it, list every file and ADR that depended on it
  (search for the ID), and add a task to CURRENT.md to revisit them. Never delete the entry.
- If you notice yourself thinking "this is probably fine", that's an assumption — write it.
