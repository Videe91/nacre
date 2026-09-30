# D-0001: Decision-first development, one file per functionality

- **Status:** accepted
- **Tier:** D3
- **Relies on assumptions:** A-0001

## Context
Nacre is built mostly by AI agents across many sessions. Undocumented choices get re-made
differently each session, and large files (the previous prototype's core reached ~3,700 lines)
become impossible for agents or humans to reason about.

## Options considered
1. **Code-first, document later** — fast at first; decisions get lost, agents drift.
2. **Heavy decision register for everything** — very safe; slows shipping (previous prototype was
   blocked in spec phase for weeks).
3. **Tiered decisions + written assumptions + one functionality per file** — durable choices
   written before code, small choices captured in code, every file self-describing.

## Decision
Option 3.

## Why this one
Keeps the traceability of option 2 for choices that matter, without blocking small work.
One-file-per-functionality lets an agent load exactly one file to understand one feature.

## Consequences
Every file carries a header; a structure check runs every session; INDEX.md must stay current.

## How we'd know it was wrong
Sessions spend more time on paperwork than code, or files are split so finely that features
can no longer be understood from one file.
