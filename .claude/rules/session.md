# Session rules

## At the start of every session
1. Read `docs/state/CURRENT.md` — it is the single truth about where the project is.
2. Read the spec section and decisions relevant to the task.
3. Open `docs/modules/INDEX.md` to find which file owns the functionality you will touch.
4. Open the actual code before making any claim about it.

## At the end of every session (handoff)
1. Update `docs/state/CURRENT.md`: phase, what changed, what's next, blockers.
2. Record any new decisions (ADR) and assumptions (A-xxxx).
3. Update `docs/modules/INDEX.md` if files were added, split or renamed.
4. Run `python scripts/check_structure.py` and `pytest`. Record exact results, including failures.
5. Commit message references IDs, e.g. `ledger: add seal chain (D-0004, A-0003)`.

**If CURRENT.md is out of date, fixing it is the first task — before any code.**
