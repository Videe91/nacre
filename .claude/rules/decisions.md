# Decision rules

Every non-trivial choice is written. Use the tier to decide *where*.

| Tier | What | Where it's written | Approval |
|---|---|---|---|
| D0 | Tiny implementation detail (variable names, loop style) | Code + tests | None |
| D1 | Local design inside one module | That file's header "Notes" + CURRENT.md | None |
| D2 | Durable: persistence format, public interface, invariants, dependencies, cross-module behaviour, benchmark method | New ADR in `docs/decisions/` BEFORE coding | Owner reviews |
| D3 | Constitutional: privacy/security boundaries, scope isolation, what counts as proof | New ADR BEFORE coding | Owner must approve explicitly |

Rules:
- Use `docs/decisions/TEMPLATE.md`. File name: `NNNN-short-title.md`, ID `D-NNNN`.
- Every ADR lists the **alternatives considered** and **why they lost** (there is always more than one way).
- Every ADR lists the **assumptions it relies on** (A-IDs).
- Never edit an accepted ADR's decision. Supersede it with a new ADR and link both ways.
- If code needs a D2/D3 choice that has no ADR: STOP, write a *proposed* ADR, ask the owner.
