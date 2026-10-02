"""
Functionality: Measure, on the EXP-0004 dev split only, the contradiction links the sleep pass made in one scope:
  the correct-link rate and the false-link rate (D-0030 owner condition 3), with their raw counts.
Owns: reading the judged links and the beliefs' supporting outcomes from the ledger, the true-conflict pairs from the
  grading refs, both rates, and summing per-scope rows into run totals.
Public entry: measure_links(), summarize_link_rows(), write_link_rows(), t3_families()
Decisions: D-0030, D-0016, D-0017
Assumptions: A-0047, A-0048
Notes: EVALUATION HARNESS ONLY, called after each scope's history in the dev coverage mode
  (eval/run_exp0004_replicate.py). Grading refs are read here and nowhere in the product or the arms; nothing here
  writes to the ledger or reaches a prompt.
  - A T3 family (grading_refs.v1_event_ids / v2_event_ids, dataset ids mapped to this run's ledger ids, families
    de-duplicated by their id sets) yields the true conflicts: every (v1 event, v2 event) pair.
  - A judged link (`contradiction_proposed` with `link: "judged"`) links the pair (s, o) for every outcome s that
    supports its target belief (a `support` edge of any version of it) and its episode outcome o
    (episode_span.outcome_id).
  - correct-link rate = true conflicts linked / all true conflicts.
  - false-link rate = false links / all candidate pairs judged. A false link is a judged link that links no true
    conflict of this scope. "Pairs judged" = the (episode, candidate belief) pairs shown to the judge, from the
    sleep reports of this scope's history (SleepReport.judge_pairs): D1, because a judge request does not record
    which beliefs it showed outside the prompt text.
  - A rate with a zero denominator is None (never 0 or 1). Rates are also given as exact "num/den" strings.
  - Explicit links (`link: "explicit"`) are counted separately; EXP-0004 histories carry none.
"""
import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from uuid import UUID

from nacre.core.root_key_provider import RootKeyProvider
from nacre.ledger.read_stream import read_stream
from nacre.scopes.open_scoped_session import ScopedSession

COUNTS = ("true_conflicts", "true_conflicts_linked", "links", "false_links", "pairs_judged", "explicit_links")


def t3_families(grading: Iterable, scope_id: str, events: Mapping[str, UUID]) -> list[tuple[frozenset, frozenset]]:
    """The scope's T3 families as (v1 ledger ids, v2 ledger ids), de-duplicated (dataset ids mapped via `events`)."""
    fams = []
    for g in grading:
        if g.scope_id != scope_id or g.type != "T3":
            continue
        fam = tuple(frozenset(events[x] for x in g.refs.get(k) or () if x in events)
                    for k in ("v1_event_ids", "v2_event_ids"))
        if fam[0] and fam[1] and fam not in fams:
            fams.append(fam)
    return fams


def _rate(num: int, den: int) -> tuple[float | None, str | None]:
    return (None, None) if den == 0 else (num / den, f"{num}/{den}")


def _with_rates(row: dict) -> dict:
    row["correct_link_rate"], row["correct_link_fraction"] = _rate(row["true_conflicts_linked"], row["true_conflicts"])
    row["false_link_rate"], row["false_link_fraction"] = _rate(row["false_links"], row["pairs_judged"])
    return row


def measure_links(session: ScopedSession, key_provider: RootKeyProvider, stream_id: UUID,
                  families: list[tuple[frozenset, frozenset]], pairs_judged: int) -> dict:
    """The counts and both rates for one scope's stream (read through `session`)."""
    support: dict[str, set] = {}
    links = []
    explicit = 0
    for e in read_stream(session, key_provider, stream_id):
        c = e.body.get("content") if isinstance(e.body, dict) else None
        if not isinstance(c, dict):
            continue
        if c.get("op") == "version" and c.get("kind") == "belief":
            support.setdefault(c["object_id"], set()).update(
                UUID(x["target_event_id"]) for x in c["edges"] if x["role"] == "support" and x["target_event_id"])
        elif c.get("op") == "contradiction_proposed" and c.get("link") == "judged":
            links.append((c["target_object_id"], UUID(c["episode_span"]["outcome_id"])))
        elif c.get("op") == "contradiction_proposed" and c.get("link") == "explicit":
            explicit += 1
    linked_pairs = {(s, o) for obj, o in links for s in support.get(obj, ())}
    true_pairs = {(v1, v2) for v1s, v2s in families for v1 in v1s for v2 in v2s}
    false = sum(1 for obj, o in links if not any((s, o) in true_pairs for s in support.get(obj, ())))
    return _with_rates({"true_conflicts": len(true_pairs), "true_conflicts_linked": len(true_pairs & linked_pairs),
                        "links": len(links), "false_links": false, "pairs_judged": pairs_judged,
                        "explicit_links": explicit})


def summarize_link_rows(rows: list[dict]) -> dict:
    """Run totals over per-scope rows (pooled across scopes and replicates), with both rates."""
    return _with_rates({k: sum(r[k] for r in rows) for k in COUNTS} | {"scopes": len(rows)})


def write_link_rows(run_dir: Path, rows: list[dict]) -> dict:
    """Write `rows` to <run_dir>/link_rows.json (beside coverage_rows.json); return the run totals."""
    (Path(run_dir) / "link_rows.json").write_text(json.dumps(rows, indent=1))
    return summarize_link_rows(rows)
