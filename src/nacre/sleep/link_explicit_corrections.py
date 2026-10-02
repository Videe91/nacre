"""
Functionality: D-0020's explicit trigger: turn every trusted `correction` whose `correction_of` names a
  belief-version event into one contradiction link against that belief's head, deterministically, once.
Owns: finding the stream's corrections of belief versions, the trust and head checks, idempotency (one explicit link
  per correction, found in the ledger), one transaction per correction, and the content-free counts.
Public entry: link_explicit_corrections(), EXPLICIT_REASONS
Decisions: D-0020, D-0030, D-0017, D-0023
Assumptions: none
Notes: D-0030 decision, option E ("still built, as a deterministic path, no model"). No model call.
  - A candidate is a `correction` event of this stream with a readable body whose single `correction_of` names a
    version event of kind `belief` in this stream. Corrections of any other event are not this trigger (not counted).
  - Trusted = the envelope's trust is `trusted` (D-0012: derived from source and authorship; text never grants it).
    D1: no further authority rule (D-0020 says "a trusted correction"). Untrusted -> `untrusted`, no link.
  - The link targets the belief's CURRENT head (stores/propose_contradiction.py, `link: "explicit"`), never a
    superseded one (`head_superseded`); an unreadable head (shredded) -> `head_unreadable`.
  - Idempotent from the ledger: a correction that already has an explicit link is skipped (not counted), so a re-run
    appends nothing. Each correction is linked in its OWN committed session, so a D-0023 refusal of one
    (AppendError from ContributorError) rolls back only that one and is counted as `refused`.
  - The link never contests by itself: contest_belief does not count explicit links (they carry no decision), so this
    file never calls it. Supersession is not built (D-0030 S1).
"""
from collections import Counter
from collections.abc import Callable
from contextlib import AbstractContextManager
from uuid import UUID

from nacre.core.event import EventType, Trust
from nacre.core.root_key_provider import RootKeyProvider
from nacre.keys.derive_contributor_key import ContributorError
from nacre.ledger.read_stream import read_stream
from nacre.ledger.validate_append import AppendError
from nacre.scopes.open_scoped_session import ScopedSession
from nacre.sleep.find_contradiction_candidates import read_belief_heads
from nacre.stores.propose_contradiction import propose_contradiction
from nacre.stores.write_version import read_version_events

EXPLICIT_REASONS = frozenset({"linked", "untrusted", "head_superseded", "head_unreadable", "refused"})


def _content(e) -> dict | None:
    c = e.body.get("content") if isinstance(e.body, dict) else None
    return c if isinstance(c, dict) else None


def link_explicit_corrections(open_session: Callable[[], AbstractContextManager[ScopedSession]],
                              key_provider: RootKeyProvider, stream_id: UUID, *, run_id: UUID) -> Counter:
    """Link every not-yet-linked correction of a belief version in `stream_id`; return the counts by reason."""
    with open_session() as s:
        events = read_stream(s, key_provider, stream_id)
        belief_of = {v.envelope.event_id: UUID(v.body["content"]["object_id"])
                     for v in read_version_events(s, key_provider, stream_id) if v.body["content"]["kind"] == "belief"}
        heads = read_belief_heads(s, key_provider, stream_id)
    done = {c["correction_id"] for e in events if (c := _content(e)) and c.get("op") == "contradiction_proposed"
            and c.get("link") == "explicit"}
    counts: Counter = Counter()
    for e in events:
        c = _content(e)
        if e.envelope.event_type != EventType.CORRECTION or c is None or str(e.envelope.event_id) in done:
            continue
        named = [UUID(r["event_id"]) for r in c.get("refs", []) if r["rel"] == "correction_of"]
        if len(named) != 1 or named[0] not in belief_of:
            continue
        if e.envelope.trust != Trust.TRUSTED:
            counts["untrusted"] += 1
            continue
        head = heads.get(belief_of[named[0]])
        if head is None:
            counts["head_unreadable"] += 1
            continue
        if head.status == "superseded":
            counts["head_superseded"] += 1
            continue
        try:
            with open_session() as s:
                propose_contradiction(s, key_provider, stream_id=stream_id, belief_object_id=head.object_id,
                                      correction_id=e.envelope.event_id, text=c["text"], run_id=run_id)
        except AppendError as exc:
            if not isinstance(exc.__cause__, ContributorError):
                raise
            counts["refused"] += 1
            continue
        counts["linked"] += 1
    return counts
