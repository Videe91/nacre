# Assumptions register

| ID | Assumption | Why we believe it | How we'll test it | If wrong | Status |
|---|---|---|---|---|---|
| A-0001 | Agents follow repo rules reliably when rules live in CLAUDE.md + .claude/rules | Worked on the previous prototype | check_structure.py passes every session | Add hooks / CI enforcement | open |
| A-0002 | Postgres (append-only + row-level security) is enough for ledger, scopes and links through phase 3 | Proven pattern; MNEXA ran on SQLite | Phase 1–3 benchmarks and query latency | Add a graph store beside Postgres | open |
| A-0003 | MNEXA's proven results reproduce after porting to the new architecture | Same mechanisms, same frozen tasks | Regression tier (phase 2 gate) | Investigate port drift before any new features | open |
| A-0004 | Python is fast enough; model and DB calls dominate latency | Typical agent workloads | Profile in phase 3 | Rewrite hot paths in Rust | open |
| A-0005 | Tests, CI and merges are reliable outcome signals for coding memory | Objective, automatic | Phase 5 coding track | Add human/reviewer signals | open |
| A-0006 | Coding agents will adopt memory through MCP with minimal setup | MCP is supported by major coding tools | Early user installs | Ship native plugins per tool | open |
