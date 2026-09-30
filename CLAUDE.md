# Nacre — Engineering Constitution

Nacre is a model-independent memory system for AI agents (coding agents first).
The full design lives in `docs/spec/SPEC.md`. Read it before any design work.

## Three laws of this repo

1. **Every decision is written down.** No durable choice exists only in code or chat.
   → `docs/decisions/` (rules: `.claude/rules/decisions.md`)
2. **Every assumption is written down.** Anything we believe but haven't proven gets an ID,
   a way to test it, and a status. → `docs/assumptions/ASSUMPTIONS.md` (rules: `.claude/rules/assumptions.md`)
3. **One file = one functionality, end to end.** No god-files. Each file does one job completely
   and declares which decisions and assumptions it depends on. → `.claude/rules/modules.md`

## Source-of-truth order (when things conflict, STOP and ask — never silently pick one)

1. `docs/spec/SPEC.md` — what we are building
2. `docs/decisions/` — why durable choices were made
3. `docs/assumptions/ASSUMPTIONS.md` — what we are betting on
4. `docs/modules/INDEX.md` — which file owns which functionality
5. Code and tests

## Every session

Start: follow `.claude/rules/session.md` (read CURRENT.md first).
End: update `docs/state/CURRENT.md` and run `python scripts/check_structure.py`.

## Core principle

> Claude may write code freely inside recorded decisions. Claude may not make a durable
> decision, or rely on a new assumption, without writing it down first.
> repo rules take priority over global ones always

