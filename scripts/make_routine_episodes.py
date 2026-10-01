"""
Generate the frozen ROUTINE episode set for Phase 2 gate item 7 (selectivity), D-0019 amendment R3.

Routine = the prediction was correct, the outcome was as expected, there is no correction and no stakes tag. Drawn
from its own vocabulary (not from MNEXA 003-016). Deterministic (seeded); the JSON it writes is frozen by sha256 and
regenerating must reproduce it byte for byte.

Run: python scripts/make_routine_episodes.py   (writes tests/regression/routine/routine_episodes_v1.json + .sha256)
"""
import hashlib
import json
import random
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "tests" / "regression" / "routine" / "routine_episodes_v1.json"
SEED = 20261001
N = 200

SYSTEMS = ["billing-api", "auth-service", "search-indexer", "mobile-app", "data-pipeline", "web-frontend",
           "notification-worker", "report-builder", "cache-layer", "payments-gateway"]
SUCCESS_ACTIONS = [("run the unit tests after the refactor", "all tests pass", "42 passed, 0 failed"),
                   ("deploy the patch to staging", "staging deploy succeeds", "deploy finished, health checks green"),
                   ("rebuild the search index", "index rebuild completes", "indexed 18,204 documents"),
                   ("bump the logging library minor version", "build stays green", "build #2291 succeeded"),
                   ("rotate the read replica", "replica rotation completes without errors", "replica promoted, lag 0s"),
                   ("run the linter on the changed files", "no lint errors", "0 problems"),
                   ("apply the reviewed schema migration", "migration applies cleanly", "migration 0042 applied"),
                   ("restart the stuck worker", "worker resumes processing", "queue depth back to 0"),
                   ("merge the approved pull request", "merge succeeds", "merged, CI green on main"),
                   ("regenerate the API client", "client compiles", "codegen complete, 0 diffs in tests")]
FAIL_ACTIONS = [("run the new regression test before the fix", "the test fails, reproducing the bug", "1 failed: expected 3, got 2"),
                ("run the canary query against the deprecated endpoint", "the endpoint returns 410 Gone", "HTTP 410"),
                ("try the old credentials after rotation", "authentication is refused", "401 Unauthorized"),
                ("run the load test above the documented limit", "requests are rate limited", "429 on 12% of requests"),
                ("check the feature flag before rollout", "the flag is still off", "flag disabled")]
DIAGNOSTICS = ["duration 41s", "runner ubuntu-22.04", "cache hit rate 93%", "no warnings", "2 retries on network fetch",
               "artifact size 4.1 MB", "peak memory 512 MB"]
SOURCES = [("ci", "integration_result", "system"), ("tool", "external", "tool"), ("review", "integration_result", "system")]


def build():
    rng = random.Random(SEED)
    episodes = []
    for i in range(N):
        system = rng.choice(SYSTEMS)
        expected_failure = rng.random() < 0.25
        action, expected, observed = rng.choice(FAIL_ACTIONS if expected_failure else SUCCESS_ACTIONS)
        source, authorship, actor = rng.choice(SOURCES)
        sections = [{"role": "status", "text": f"{system}: {'FAIL (as expected)' if expected_failure else 'PASS'}"},
                    {"role": "evaluation", "text": observed}]       # observed results are evaluations, pass or fail
        if rng.random() < 0.5:
            sections.append({"role": "diagnostic", "text": rng.choice(DIAGNOSTICS)})
        if rng.random() < 0.2:
            sections.append({"role": "operator_note", "text": f"routine {action.split()[0]} on {system}"})
        episodes.append({
            "id": f"routine-{i:03d}",
            "decision_text": f"On {system}, {action}.",
            "prediction": {"expected_outcome": expected, "expected_success": not expected_failure,
                           "confidence_pct": rng.choice([70, 80, 90, 95])},
            "outcome": {"success": not expected_failure, "source": source, "authorship": authorship, "actor_kind": actor,
                        "sections": sections}})
    return {"version": "routine_episodes_v1", "seed": SEED, "definition": "prediction correct, expected outcome, "
            "no correction section, no stakes tags; not drawn from MNEXA 003-016", "episodes": episodes}


def main():
    data = json.dumps(build(), indent=1) + "\n"
    OUT.write_text(data)
    OUT.with_suffix(".sha256").write_text(hashlib.sha256(data.encode()).hexdigest() + "\n")
    print(OUT, hashlib.sha256(data.encode()).hexdigest())


if __name__ == "__main__":
    main()
