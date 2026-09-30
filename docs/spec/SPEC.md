# Agent memory system — final build spec

Sep 30, 2026 · @Vineet Pandey

## Overview

A model-independent memory system for AI agents: MNEXA's proven truth engine inside Nacre's living, scoped body, extended with five nature-inspired capabilities. It works with any model, per user and per project, for general and coding agents.

The laws every layer follows:

1. **The ledger is the only source of truth.** Everything else is derived and rebuildable from it.
2. **Models propose; evidence promotes.** No model can silently declare knowledge, settings, or skills.
3. **No knowledge without ancestry.** Every memory traces to grounded evidence in the ledger.
4. **Memory prepares, the model reasons.** Memory freezes a context; any model reasons over it outside the memory system.
5. **Scoped by default.** User, project and team memory are isolated; sharing is explicit promotion.
6. **Outcome-first.** A mechanism ships only if it measurably improves task results against a frozen evaluation.
7. **Decision-first, but light.** Durable choices (persistence, privacy, interfaces, benchmark method) get a decision record; everything else is captured in code and tests.

This build is new, but not from zero: every proven MNEXA mechanism is ported as-is, and MNEXA's frozen experiments become the regression suite. If the new build can't reproduce MNEXA's results, it isn't done.

## Lineage

What each part inherits, and how much of it is already proven.

| Part | From MNEXA (proven in repo) | From Nacre (designed) | New extension |
| --- | --- | --- | --- |
| Ledger | Append-only commits, AS\_OF(N) snapshots, idempotent writes (ADR-0018), experience vs. interpretation planes (ADR-0002), four time concepts (ADR-0010) | Postgres, scoped streams, fingerprint chain, crypto-shredding, attachments | Typed multimodal payloads |
| Capture | Decision, prediction, action, outcome evidence (ADR-0016) | Predict-first line per action | Predictor supplies expected outcome |
| Write gate | Proposal → evidence-gated promotion (ledger 36–37) | Surprise + stakes scoring | Learning modes shift thresholds |
| Sleep pass | Evidence-disciplined, span-grounded, role-gated, atomic consolidation (seed growth 005–016) | Decay, protection, habituation, pattern compression | Self-tuned parameters |
| Stores | Beliefs with contestation and supersession (ledger 38–39), episodes | Skills, threats, directory, links | Cognitive map anchors |
| Recall | Identity narrows, similarity ranks; hierarchical addressing; evidence quorum; proposition-local scoring (seed growth 018–027) | Scope merging, gap-awareness, simulate-before-act | Map-location recall, predictor checks |
| Reasoning boundary | Frozen ContextFrame; model reasons outside (ADR-0017); cross-model transplant proof (ledger 41) | — | Personal model for routine judgment |
| Proof | Frozen tasks, controlled ablations, null results kept | Head-to-head vs. Mem0, Zep, Letta; real multi-week tracks | Self-tuning uses the same eval |

## Architecture at a glance

Evidence flows down the left from the model into the ledger and through the gate and sleep pass into the stores; recall flows up the right as a frozen context back to any model.

&#91;embedded content: architecture · 10 components\]

The three extensions at the bottom read the ledger and stores but never write truth directly: the predictor feeds expected outcomes to capture and recall, the self-tuner adjusts gate, sleep and recall settings, and the personal model handles routine judgment.

## The ledger

The ledger records what happened, when, who did it and where it came from, and proves it was never changed. It is deliberately dumb: record, seal, replay. All intelligence lives above it.

**Two planes (MNEXA ADR-0002).** The experience plane holds what happened and is immutable. The interpretation plane holds what the system concluded (beliefs, skills, threats), versioned and always linked back to experience.

**Event types:** message, action, result, prediction, decision, outcome, statement (a person states something directly), memory event (proposal, promotion, contestation, supersession), config event (settings and mode changes), correction (points to an earlier event; nothing is edited), deletion marker.

| Field | What it is | Why |
| --- | --- | --- |
| event\_id | Time-sortable unique id | Ordering and lookup |
| scope | org / user / project / task ids | Isolation |
| commit\_seq | Gapless sequence per stream | AS\_OF(N) snapshots; consumers keep bookmarks |
| occurred\_at / recorded\_at | Two clocks (plus ADR-0010 concepts) | Late arrivals, correct ordering |
| actor | Person, agent, model name and version, tool | Which brain did what |
| source + trust | Origin (chat, git, CI, web) and trusted / untrusted | Injection firewall: untrusted content is never an instruction |
| caused\_by / cycle\_id | Causal parent; cognitive cycle it belongs to | Replay a task as cause and effect |
| payload\_type | text, image, audio, diff, table, structured, trace | Any-content memory |
| payload / attachment\_ref | Content, or fingerprint pointer to object storage | Keeps the ledger fast; dedup |
| config\_version / mode | Settings and learning mode active at the time | Self-tuning and state-dependent recall |
| idempotency\_key + request hash | Per logical write | Safe retries (ADR-0018) |
| prev\_hash / hash | Fingerprint chain per stream | Tamper evidence |
| key\_id | Encryption key for this scope | Crypto-shredding |

**Never edited, enforced by the machine.** The application role can only insert. A nightly job verifies the fingerprint chain. Corrections are new events.

**Deletion without editing.** Payloads are encrypted per scope key. Destroying the key makes the data unreadable while the chain stays valid; a deletion marker records it; derived memories and personal-model adapters are rebuilt without it. Secrets are stripped before any write.

**Write path:** agent → MCP/API → intake validates, strips secrets, tags trust → assigns sequence and seal → single atomic transaction → attachments to object storage by fingerprint.

**Read path:** stream by scope from sequence N; replay one cycle; AS\_OF(N) snapshot for recall; full rebuild of any derived store from sequence 1.

**Storage:** Postgres events table partitioned by month, indexed by (scope, seq), cycle, and (type, time); S3-compatible object storage for attachments; old partitions archived, still replayable.

## Scopes

Every event and memory carries a scope. Recall merges scopes from narrowest outward: task → project → user → team.

| Scope | Holds | Readable by | Example |
| --- | --- | --- | --- |
| Task | Working memory for one cycle | That cycle | "Refactoring the auth module now" |
| Project | Beliefs, skills, threats, map for one project or repo | That project's agents and people | "Never hand-edit the migrations folder" |
| User | Preferences, style, cross-project habits | That user's agents | "Prefers small PRs with plain summaries" |
| Team / org | Conventions, approved skills, directory | Whole org | "All services log in JSON" |
| Agent | Local know-how of one tool or sub-agent | That agent | "Deploy agent's retry recipe for flaky CI" |

1. **Isolation is enforced by the database** (row-level security per scope), not by prompts.
2. **Narrowest wins on conflict;** the conflict itself is logged.
3. **Promotion upward is explicit:** a pattern seen in 3+ projects becomes a proposal for user or team scope; a person approves it (or auto-approval with human veto, see open decisions).
4. **One stream per scope,** so deleting a scope removes its stream, its keys and everything derived.
5. **New projects inherit a starter kit** from user and team scope, never raw memories from other projects.

## Memory lifecycle

Experience becomes knowledge in four steps, and every step leaves a ledger event.

1. **Capture.** Each cycle records decision, prediction, action and outcome as separate evidence (ADR-0016). The expected outcome comes from the agent and, once built, the predictor. Absence of an outcome is absence of evidence, never failure.
2. **Write gate.** Scores each event on surprise (predicted vs. actual), stakes (money, clients, production, irreversible), and direct statements from people. The current learning mode shifts the threshold. Events above it are flagged; nothing is promoted here.
3. **Sleep pass.** Runs offline per scope, only where flagged events exist (immediately after an incident in incident mode):
   - Proposes lessons from flagged events using evidence-disciplined consolidation.
   - Grounds every claim in exact evidence spans (text span, diff hunk, image region, audio range), with an authoritative-role gate and atomic propositions.
   - Admits closed-world only: unsupported claims are rejected; structurally unclear ones become fallback records, never invented structure.
   - Merges duplicates, contests contradictions, supersedes outdated beliefs with history kept, compresses repeats into patterns, applies decay.
   - A cheap model does the work; a different model checks it; promotion is evidence-gated.
4. **Stores.** Promoted interpretations land in five linked stores, all on the interpretation plane: beliefs (current truth, versioned, contestable), episodes (pointers into the ledger), skills (recipes that succeeded), threats (failures and attacks, checked before risky actions), directory (who or what holds which knowledge). Memories anchor to places on the cognitive map.

**Memory record fields**

| Field | Holds | Nature parallel |
| --- | --- | --- |
| id, version, scope, type | Identity, version, isolation, store | Separate memory systems |
| nucleus / support span | Retrieval handle vs. authoritative meaning (MNEXA 013, 021) | Cue vs. content |
| ancestry | Ledger event ids and exact spans | Every memory traceable |
| origin | stated / observed / inferred | Fact vs. guess |
| confidence | 0–1 | Feeling of knowing |
| status | proposed / active / contested / superseded / fallback | Belief revision |
| strength, protected, habituated | Use-based strength; never decays; muted | Use it or lose it; elephant; Mimosa |
| salience, mode\_at\_creation | Surprise + stakes; learning mode | Amygdala; state-dependent memory |
| map\_anchors, links | Places on the map; related memories | Place cells; association |
| use\_count, helped\_count, last\_used | Outcome stats | Reinforcement |

## Recall and the reasoning boundary

Recall hands the model only what the task needs, frozen at a fixed snapshot; the model reasons outside the memory system.

**Recall pipeline (the attention firewall):**

1. **Freeze the snapshot.** Bind AS\_OF(N) once per cognitive cycle before retrieval (ADR-0011, ADR-0013). Recall is read-only.
2. **Scope merge.** Candidate pool = task → project → user → team, narrowest wins.
3. **Identity narrows.** Exact identity addressing first (system, code, entity), degrading hierarchically (code → system → cluster → domain → global) and refined conjunctively; map location ("near this file or service") is an added address.
4. **Similarity ranks.** Three channels (semantic, lexical/exact, entity) fused; channels that can't discriminate abstain; scoring is per proposition, not per memory blob.
5. **Safe context assembly.** Pareto-safe pruning, and pruning only with a quorum of at least two active signals; otherwise keep breadth.
6. **Threat and predictor checks.** Matching threats are always included; before risky actions the predictor's expected outcome is attached.
7. **Gap awareness.** Return a confidence level; if coverage is weak, the agent asks instead of guessing.
8. **Trace.** Record the assembled context atomically (ContextAssembled, ADR-0014) so every decision can be replayed with exactly what the model saw.

**Reasoning boundary (ADR-0017).** Memory produces an immutable, hashed ContextFrame. Any model reasons over it; reasoning prompts can never change what was recalled. Decisions return through record\_external\_decision, outcomes through observe\_outcome. This is what makes the frontier model swappable, proven by the live cross-provider transplant.

## Living rules

These rules keep memory current without ever touching the ledger; they change only strength and status on the interpretation plane.

- **Usefulness decides strength.** A memory strengthens only when it was presented and the decision it informed succeeded. Presented-but-unhelpful memories weaken. Unused ones decay over time.
- **Protected memories.** Rare, high-stakes knowledge (an outage cause, a near-lawsuit) never decays. Set by the stakes score, by incident mode, or by a person.
- **Correct on recall.** After each cycle, memories contradicted by outcomes are contested, then superseded through the evidence gate. Corrections are checked against the ledger, so recall can never rewrite history.
- **Habituation.** Alerts that fire repeatedly with no bad outcome are muted per scope; they un-mute if one ever precedes a failure.
- **Shared trails.** In multi-agent work, workspace memory strengthens each time an agent uses it and evaporates when abandoned.
- **Starter kit.** New agents and projects inherit distilled instincts (conventions, top skills, top threats) from user and team scope.
- **Forgetting is not deletion.** Decayed memories drop out of recall but stay on the interpretation plane and in the ledger; physical deletion happens only through crypto-shredding.

## Five extensions

Each extension closes a gap where nature is still ahead, and each obeys the same laws: ledger events, proposals before promotion, rebuildable from the ledger.

### 1. Self-tuning (nature: evolution)

- All knobs live in a versioned config per scope: gate threshold, surprise and stakes weights, decay speed, protection threshold, recall breadth and quorum, habituation count.
- Every cycle records its config version, so outcomes trace to settings.
- An offline tuner replays past cycles from the ledger (AS\_OF snapshots) under alternative configs and measures counterfactual outcomes.
- A new config is a proposal, promoted only if it beats the current one on the frozen eval with zero regressions. Small steps per round, protected memories can't be tuned away, one-click rollback.
- Later: per-project tuning.

### 2. Learning modes (nature: dopamine and adrenaline)

| Mode | Write gate | Sleep pass | Recall |
| --- | --- | --- | --- |
| Normal | Selective | Nightly | Standard |
| Incident | Very open; everything is a protection candidate | Right after resolution | Threats first, wider |
| Onboarding | Open; starter kit loaded | Frequent | Wider; asks more |
| Exploration | Failed attempts recorded as lessons | Nightly | Includes weaker memories |

- Triggered explicitly (incident declared, CI outage) or automatically (surprise rate spikes above baseline). Time-boxed; returns to normal automatically.
- Modes are presets over the self-tuning knobs. Memories record their creation mode, so incident lessons recall more strongly in the next incident (state-dependent memory).

### 3. Any content and cognitive maps (nature: senses and place cells)

- Typed ledger payloads: text, image, audio, diff, table, structured, trace. Each stored by fingerprint with a description, its own index and extracted entities.
- Evidence grounding extends across types: text spans, image regions, diff hunks, audio time ranges, table cells.
- A versioned cognitive map of the agent's world (coding: modules, files, services, dependencies; business: customers, processes, systems), built automatically from events.
- Memories anchor to map places; recall can search by location, extending "identity narrows" from names to neighbourhoods.

### 4. The predictor (nature: world model)

- One interface: predict(situation, action) → expected outcome + confidence.
- v1 case-based: combine outcomes of similar past actions at the same map place; no training.
- v2 learned: a small model trained on the ledger's prediction, action, outcome records.
- v3 causal: learned "changing X caused Y" links across map places.
- Uses: honest surprise scores for the gate; simulate-before-act warnings; "what if" answers beyond stored facts. Its calibration is tracked and its vote weighted by it.

### 5. Personal model (nature: sleep rewiring neurons)

- Two brains: a swappable frontier model reasons; a small open-weights personal model handles routine judgment (gate scoring, recall reranking, repeated decisions, predictor v2).
- Trained per scope as adapters from promoted beliefs, successful skills and outcome-labelled decisions. Swappable and deletable; deletion retrains without shredded data.
- Anti-forgetting: every training run replays old consolidated knowledge; protected memories are always in the replay set; each version must pass the regression suite before replacing the last.
- Its outputs are proposals, never truth; it is always rebuildable from the ledger.

## Coding agent profile

The core is identical for coding agents; only inputs and store contents change. Coding is the fastest place to prove every gate, because tests, CI and review give hard outcome signals.

| Layer | For a coding agent |
| --- | --- |
| Ledger | Commands, diffs, test runs, CI runs, review comments, commit hashes (git, CI and review webhooks as trusted system sources) |
| Capture | "Expect 3 tests to change, build passes" before each change |
| Write gate | Failing tests after a "safe" change, reverts, prod incidents, rejected reviews, developer statements |
| Beliefs | Stack, versions, architecture decisions and why, module owners |
| Episodes | Pointers to commits, PRs, CI runs, incident threads |
| Skills | Proven recipes: "add an endpoint in this repo", "run the flaky suite" |
| Threats | Fragile files, flaky tests, past security findings, forbidden commands |
| Directory | Which agent or person owns which module; where the API contract lives |
| Cognitive map | Repo → packages → modules → files, plus service dependencies |
| Recall | Given a ticket: beliefs, skills and threats for the places it will touch |

1. **Code is the truth for code.** Memories about code are re-checked against the current repo at recall; if the code moved on, the memory is marked stale.
2. **Decisions over details.** Store why a choice was made, not what a function does.
3. **Outcome = tests + CI + review + merge.**
4. **Project scope = repo scope** by default; monorepos can scope per package.

## Tech stack

Python core, Postgres storage, MCP as the plug; Rust only where the benchmark proves a speed need.

| Part | Choice | Why |
| --- | --- | --- |
| Core service (gate, sleep, recall, tuner) | Python | AI and eval ecosystem is Python-first; MNEXA code ports directly |
| Ledger, interpretations, links, config | Postgres + pgvector | Append-only enforcement, row-level security per scope, versioned records, similarity search in one database |
| Attachments | S3-compatible object storage | Content-addressed, deduplicated |
| Agent interface | MCP server + Python SDK | Any agent or model plugs in without custom code |
| Coding inputs | Git, CI and review webhooks | Trusted outcome signals |
| Personal model | Python (PyTorch, adapter training) on open-weights models | Only practical ecosystem |
| Hot paths later | Rust | Log ingest or recall, only if benchmark shows need |
| Graph database | Deferred | Links start as Postgres tables; add a graph store beside Postgres only if deep multi-hop queries prove necessary |

## Proof

Three tiers, each frozen before the mechanism it judges is built, with null and negative results kept.

1. **Regression (MNEXA parity).** MNEXA's frozen task sets (seed growth 003–029) and unit suites must reproduce at the same or better rates on the new build. Gate for porting.
2. **Comparability.** LongMemEval and LoCoMo scores against Mem0, Zep and Letta, same model, independently re-run, so results speak the market's language.
3. **Headline: real multi-week tracks.** A coding track (a real repo evolving over 4–6 simulated weeks of tickets, with decisions that change midway) and a general-work track (shifting client requirements, people, deadlines). Contenders: no memory, Mem0, Zep, Letta, this system, all on the same model.

Built-in traps: facts that change mid-run, a planted malicious instruction, a rare critical event in week 1 that matters in week 5, repeated harmless alerts, a model swap mid-run.

| Metric | Measures |
| --- | --- |
| Task success | Tests pass and merged (coding); rubric-graded deliverable (general) |
| Stale-fact errors | Actions based on superseded beliefs |
| Repeat failures | Same mistake twice |
| Asks vs. wrong guesses | Gap awareness |
| Injection resistance | Planted instruction never causes an action |
| Transplant retention | Success kept after mid-run model swap |
| Cost | Tokens and dollars per completed task |

Win condition: higher task success and fewer stale-fact and repeat errors than every contender, at equal or lower cost. The same frozen evaluation is what the self-tuner optimizes against, with hidden cases kept sealed from it (ADR-0004).

## Build phases

Six phases, each gated by a frozen test; a phase that fails its gate is fixed before the next begins.

&#91;embedded content: build phases · 6 phases, 6 gates\]

Phases 1–3 rebuild and harden what MNEXA has proven; phases 4–6 add what's new. Typed payloads go into phase 1 because changing the ledger later is painful; everything else arrives when its evidence can be measured.

## Risks and open decisions

**Risks**

- **Port drift.** Re-implementing MNEXA mechanisms can silently lose proven behaviour. Mitigation: regression tier gates every port.
- **Consolidation errors.** Mitigated by the checker model, evidence-gated promotion, and full rebuild from the ledger.
- **Outcome signals for general work** are weaker than tests and CI; needs explicit done/failed markers or ratings.
- **Self-tuner overfitting the eval.** Mitigated by sealed hidden cases, small steps and regression gates.
- **Sleep and tuner cost.** Run only on scopes with new flagged events; cheap models for routine work.
- **Personal model** is research-grade; it ships only if it cuts cost at equal success.
- **Process weight.** Keep decision records for durable choices only, and keep the state file current so agents always start from the real project state.

**Open decisions (need a decision record before building)**

- Cross-scope promotion: always human, or auto after N projects with human veto.
- Crypto-shredding key granularity: per user, per project, or per memory.
- Links storage: Postgres tables only, or a graph store beside Postgres (decide from phase 3 query patterns).
- External side-effect durability (MNEXA D-33): the dual-commit problem when an action reaches outside systems.
- Credit attribution (MNEXA D-30): how outcome credit is shared across the memories that informed a decision; needed for strength updates.
- Learning-mode auto-trigger thresholds.
- Recall latency target for interactive agents.
