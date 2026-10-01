"""Tests for stores/propose_lesson.py: exact spans, real ancestry, trusted-correction grounding."""
import uuid

import pytest

from stores_kit import RULE, episode, propose
from nacre.ledger.read_stream import read_stream
from nacre.stores.propose_lesson import ProposalError, Qualifier, propose_lesson


def _content(s, provider, stream, eid):
    return [e for e in read_stream(s, provider, stream) if e.envelope.event_id == eid][0].body["content"]


def test_a_grounded_proposal_records_its_span_key_and_trust(rw, provider, streams):
    a = streams["a"]
    with rw() as s:
        d, o = episode(s, provider, a)
        c = _content(s, provider, a, propose(s, provider, a, d, o))
    assert c["support_text"] == RULE and c["authority"] == "proposal_only" and c["grounded_in_trusted_correction"]
    assert c["key"] == "retry vx-41 failures after 137 ms" and c["structure_status"] == "structured"


def test_untrusted_correction_is_recorded_but_not_trusted(rw, provider, streams):
    a = streams["a"]
    with rw() as s:
        d, o = episode(s, provider, a, trusted=False)
        assert _content(s, provider, a, propose(s, provider, a, d, o))["grounded_in_trusted_correction"] is False


@pytest.mark.parametrize("kw,match", [
    ({"span": (0, 999)}, "inside the section"), ({"span": (5, 5)}, "inside the section"),
    ({"section_index": 7}, "out of range"), ({"nucleus": "not in the span"}, "nucleus"),
    ({"qualifiers": (Qualifier("timing", "137 ms"),)}, "qualifiers"), ({"qualifiers": (Qualifier("condition", "absent"),)}, "qualifiers"),
])
def test_ungrounded_proposals_are_refused(rw, provider, streams, kw, match):
    a = streams["a"]
    with rw() as s:
        d, o = episode(s, provider, a)
        args = dict(stream_id=a, decision_id=d, outcome_id=o, section_index=1, span=(0, len(RULE)), nucleus="Retry VX-41")
        args.update(kw)
        with pytest.raises(ProposalError, match=match):
            propose_lesson(s, provider, **args)


def test_ancestry_must_be_real(rw, provider, streams):
    a = streams["a"]
    with rw() as s:
        d1, o1 = episode(s, provider, a)
        d2, _ = episode(s, provider, a)
        with pytest.raises(ProposalError, match="not an outcome of that decision"):
            propose_lesson(s, provider, stream_id=a, decision_id=d2, outcome_id=o1, section_index=1, span=(0, 5), nucleus=None)
        with pytest.raises(ProposalError, match="decision"):
            propose_lesson(s, provider, stream_id=a, decision_id=uuid.uuid4(), outcome_id=o1, section_index=1, span=(0, 5), nucleus=None)
