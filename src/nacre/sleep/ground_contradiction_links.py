"""
Functionality: Ground the relation judge's verdicts deterministically into contradiction links: no quote, no link.
Owns: every grounding rule and its content-free rejection reason, span resolution on both sides (quote -> unique
  offsets), and re-checking the target head, the shared address, the stream and the observed outcome at link time.
Public entry: ground_links(), GroundedLink, REJECTION_REASONS
Decisions: D-0030, D-0020, D-0018, D-0017
Assumptions: A-0047, A-0048
Notes: D-0030 owner condition 2 ("no quote, no link"). No model call. Rules, in order, for each verdict:
    1. shape: belief label a string naming one of THIS call's candidates (`unknown_belief`), at most one verdict per
       belief (`duplicate_verdict`), relation one of RELATIONS (`malformed`). `unrelated` -> nothing (not a
       rejection);
    2. episode side: `section` names a section of THIS episode's bundle (`bad_section`) that is AUTHORITATIVE with
       role `correction` (`non_authoritative_section`: tool output, untrusted or other sections can never ground a
       link, so injected text cannot either); the quote is a non-empty string (`missing_episode_quote`) occurring
       exactly once in that section (`episode_quote_not_exact`, `episode_quote_not_unique`);
    3. belief side: the quote is non-empty (`missing_belief_quote`); the candidate is still the CURRENT head, read in
       the episode's transaction after its promotions: same version event (`head_changed`), not superseded
       (`head_superseded`), in this stream (`other_stream`); the quote occurs exactly once in that head's
       support_text (`belief_quote_not_exact`, `belief_quote_not_unique`);
    4. the head still shares an identity address with the episode (`no_shared_address`);
    5. the episode's decision, resolved through its action when there is one (D-0020 amendment 1,
       sleep/build_evidence_bundle.resolve_outcome_decision), has this observed outcome (`no_observed_outcome`).
  "Contiguous" holds by construction: a quote is one substring. Offsets are Python str offsets into the section text
  and into the support_text. Reasons are counted by the sleep report; there is no persisted refusal record for them
  (the only refusal record, `derived_write_refused`, is for D-0023 key refusals), so they are counted only.
  The verdict order is kept; a rejected verdict never blocks another.
"""
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from nacre.ledger.read_stream import ReadEvent
from nacre.sleep.build_evidence_bundle import BundleError, EvidenceBundle, resolve_outcome_decision
from nacre.sleep.find_contradiction_candidates import BeliefHead, Candidate
from nacre.sleep.judge_relations import RELATIONS, Verdict

REJECTION_REASONS = frozenset({
    "malformed", "unknown_belief", "duplicate_verdict", "bad_section", "non_authoritative_section",
    "missing_episode_quote", "episode_quote_not_exact", "episode_quote_not_unique", "missing_belief_quote",
    "head_changed", "head_superseded", "other_stream", "belief_quote_not_exact", "belief_quote_not_unique",
    "no_shared_address", "no_observed_outcome"})


@dataclass(frozen=True)
class GroundedLink:
    head: BeliefHead                    # the current head the link targets
    section_index: int
    episode_span: tuple[int, int]
    episode_text: str
    belief_span: tuple[int, int]
    belief_text: str


def _unique(text: str, quote: str, missing: str, prefix: str) -> tuple[int, str | None]:
    if not isinstance(quote, str) or not quote.strip():
        return -1, missing
    n = text.count(quote)
    if n != 1:
        return -1, f"{prefix}_not_exact" if n == 0 else f"{prefix}_not_unique"
    return text.index(quote), None


def _observed(bundle: EvidenceBundle, index: Mapping[UUID, ReadEvent]) -> bool:
    o = index.get(bundle.outcome_id)
    try:
        return o is not None and resolve_outcome_decision(o, index).decision_id == bundle.decision_id
    except BundleError:
        return False


def _one(v: Verdict, cand: Candidate, bundle, stream_id, heads_now, addresses, observed) -> GroundedLink | str:
    if type(v.section) is not int or not 0 <= v.section < len(bundle.sections):
        return "bad_section"
    sec = bundle.sections[v.section]
    if not (sec.authoritative and sec.role == "correction"):
        return "non_authoritative_section"
    e_start, why = _unique(sec.text, v.episode_quote, "missing_episode_quote", "episode_quote")
    if why:
        return why
    if not isinstance(v.belief_quote, str) or not v.belief_quote.strip():
        return "missing_belief_quote"
    head = heads_now.get(cand.head.object_id)
    if head is None or head.event_id != cand.head.event_id:
        return "head_superseded" if head is not None and head.status == "superseded" else "head_changed"
    if head.status == "superseded":
        return "head_superseded"
    if head.stream_id != stream_id:
        return "other_stream"
    b_start, why = _unique(head.support_text, v.belief_quote, "missing_belief_quote", "belief_quote")
    if why:
        return why
    if not head.addresses & addresses:
        return "no_shared_address"
    if not observed:
        return "no_observed_outcome"
    return GroundedLink(head, v.section, (e_start, e_start + len(v.episode_quote)), v.episode_quote,
                        (b_start, b_start + len(v.belief_quote)), v.belief_quote)


def ground_links(bundle: EvidenceBundle, stream_id: UUID, candidates: tuple[Candidate, ...], verdicts: list[Verdict],
                 heads_now: Mapping[UUID, BeliefHead], addresses: frozenset[str],
                 index: Mapping[UUID, ReadEvent]) -> tuple[list[GroundedLink], Counter]:
    """(grounded links, rejection reason counts) for `verdicts` over `candidates` (labels B1..Bk, in order)."""
    labels = {f"B{n}": c for n, c in enumerate(candidates, start=1)}
    links, rejected, seen = [], Counter(), set()
    observed = _observed(bundle, index)
    for v in verdicts:
        if not isinstance(v, Verdict) or not isinstance(v.belief, str) or v.relation not in RELATIONS:
            rejected["malformed"] += 1
            continue
        cand = labels.get(v.belief)
        if cand is None:
            rejected["unknown_belief"] += 1
            continue
        if v.belief in seen:
            rejected["duplicate_verdict"] += 1
            continue
        seen.add(v.belief)
        if v.relation == "unrelated":
            continue
        out = _one(v, cand, bundle, stream_id, heads_now, addresses, observed)
        if isinstance(out, str):
            rejected[out] += 1
        else:
            links.append(out)
    return links, rejected
