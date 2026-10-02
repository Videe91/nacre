"""Tests for stores/promote_if_supported.py: quorum, D-0017 amendment 1 (one test per safeguard), deviations 1-2."""
from stores_kit import RULE, action_episode, episode, grounded_belief, propose
from nacre.stores.contest_belief import contest_belief
from nacre.stores.promote_if_supported import QUORUM, SINGLE_SOURCE, promote_if_supported
from nacre.stores.propose_contradiction import propose_contradiction
from nacre.stores.read_heads import read_heads


def _head(s, provider, stream, oid):
    return [h for h in read_heads(s, provider, stream, include_inactive=True) if h.object_id == oid][0]


def test_amendment_trusted_and_grounded_correction_promotes_alone_as_single_source(rw, provider, streams):
    a = streams["a"]
    with rw() as s:
        p = grounded_belief(s, provider, a)
        h = _head(s, provider, a, p.object_id)
    assert (p.version, p.status, p.support, p.created) == (1, "active", "single_source", True)
    assert h.content["origin"] == "stated" and (h.content["confidence_pct"], h.content["strength_pct"]) == (50, 50)
    assert SINGLE_SOURCE["confidence_pct"] < QUORUM["confidence_pct"] and SINGLE_SOURCE["strength_pct"] < QUORUM["strength_pct"]


def test_amendment_an_untrusted_correction_does_not_promote_alone(rw, provider, streams):
    a = streams["a"]
    with rw() as s:
        d, o = episode(s, provider, a, trusted=False)
        assert promote_if_supported(s, provider, a, propose(s, provider, a, d, o)) is None


def test_amendment_trusted_but_not_grounded_in_the_correction_span_does_not_promote_alone(rw, provider, streams):
    a = streams["a"]
    with rw() as s:
        d, o = episode(s, provider, a)                                   # trusted review outcome
        status = "FAIL: VX-41 retries exhausted"
        pid = propose(s, provider, a, d, o, section=0, text=status, nucleus="VX-41 retries exhausted")   # status span
        assert promote_if_supported(s, provider, a, pid) is None
        d2, o2 = episode(s, provider, a, role="evaluation")              # trusted failing EVALUATION, not a correction
        assert promote_if_supported(s, provider, a, propose(s, provider, a, d2, o2)) is None


def test_amendment_a_second_agreeing_episode_upgrades_to_quorum(rw, provider, streams):
    a = streams["a"]
    with rw() as s:
        first = grounded_belief(s, provider, a)
        d, o = episode(s, provider, a, trusted=False)                    # outcome-inferred support also counts
        second = promote_if_supported(s, provider, a, propose(s, provider, a, d, o))
        h = _head(s, provider, a, first.object_id)
    assert (second.object_id, second.version, second.support) == (first.object_id, 2, "quorum")
    assert (h.content["confidence_pct"], h.content["strength_pct"]) == (80, 100) and len(h.content["support_decisions"]) == 2


def test_amendment_a_single_source_belief_is_contested_and_hidden_normally(rw, provider, streams):
    a = streams["a"]
    with rw() as s:
        b = grounded_belief(s, provider, a)
        for _ in range(2):
            d, _o = episode(s, provider, a, correction="VX-41 must never be retried.")
            propose_contradiction(s, provider, stream_id=a, belief_object_id=b.object_id, decision_id=d,
                                  text="VX-41 must never be retried")
        assert contest_belief(s, provider, a, b.object_id).status == "contested"
        assert all(h.object_id != b.object_id for h in read_heads(s, provider, a))


def test_amendment_outcome_inferred_lessons_still_need_two_decisions(rw, provider, streams):
    a = streams["a"]
    with rw() as s:
        d1, o1 = episode(s, provider, a, trusted=False)
        assert promote_if_supported(s, provider, a, propose(s, provider, a, d1, o1)) is None
        d2, o2 = episode(s, provider, a, trusted=False)
        p = promote_if_supported(s, provider, a, propose(s, provider, a, d2, o2))
    assert (p.version, p.support) == (1, "quorum") and p.created


def test_two_proposals_from_one_decision_count_once(rw, provider, streams):
    a = streams["a"]
    with rw() as s:
        d, o = episode(s, provider, a, trusted=False)
        propose(s, provider, a, d, o)
        assert promote_if_supported(s, provider, a, propose(s, provider, a, d, o)) is None


def test_case_and_whitespace_are_normalised_but_wording_is_not(rw, provider, streams):
    a = streams["a"]
    text2 = "retry  vx-41 FAILURES after 137 ms. Use X-Relay: cobalt."
    with rw() as s:
        d1, o1 = episode(s, provider, a, trusted=False)
        propose(s, provider, a, d1, o1)
        d2, o2 = episode(s, provider, a, correction=text2, trusted=False)
        p = promote_if_supported(s, provider, a, propose(s, provider, a, d2, o2, text=text2, nucleus="retry  vx-41 FAILURES after 137 ms"))
        assert p is not None and p.support == "quorum"
        d3, o3 = episode(s, provider, a, correction="Wait 137 ms before retrying VX-41.", trusted=False)
        q = promote_if_supported(s, provider, a, propose(s, provider, a, d3, o3, text="Wait 137 ms before retrying VX-41.",
                                                          nucleus="Wait 137 ms before retrying VX-41"))
        assert q is None


def test_deviation2_a_retry_returns_the_head_without_a_new_version(rw, provider, streams):
    a = streams["a"]
    with rw() as s:
        d, o = episode(s, provider, a)
        pid = propose(s, provider, a, d, o)
        first = promote_if_supported(s, provider, a, pid)
        again = promote_if_supported(s, provider, a, pid)
    assert (again.version, again.created) == (first.version, False)


def test_deviation1_new_support_never_reactivates_a_contested_belief(rw, provider, streams):
    a = streams["a"]
    with rw() as s:
        b = grounded_belief(s, provider, a)
        for _ in range(2):
            d, _o = episode(s, provider, a, correction="never retry")
            propose_contradiction(s, provider, stream_id=a, belief_object_id=b.object_id, decision_id=d, text="never retry")
        contest_belief(s, provider, a, b.object_id)
        d, o = episode(s, provider, a)
        p = promote_if_supported(s, provider, a, propose(s, provider, a, d, o))
        h = _head(s, provider, a, b.object_id)
    assert (p.status, p.version, h.status) == ("contested", 3, "contested")   # v1 promoted, v2 contested, v3 new support and "contradiction_decisions" in h.content


def test_unresolved_trusted_support_becomes_a_recall_eligible_fallback(rw, provider, streams):
    a = streams["a"]
    with rw() as s:
        d, o = episode(s, provider, a)
        p = promote_if_supported(s, provider, a, propose(s, provider, a, d, o, nucleus=None))
        heads = read_heads(s, provider, a)
    assert (p.status, p.support) == ("fallback", None)
    assert [(h.kind, h.status, h.content["support_text"]) for h in heads] == [("fallback", "fallback", RULE)]


def _version_content(s, provider, stream, oid, version):
    from nacre.stores.write_version import read_version_events
    (v,) = [v.body["content"] for v in read_version_events(s, provider, stream)
            if v.body["content"]["object_id"] == str(oid) and v.body["content"]["version"] == version]
    return v["content"]


def test_addresses_a_version_carries_the_sorted_union_of_its_sources_addresses(rw, provider, streams):
    a = streams["a"]
    with rw() as s:
        d, o = episode(s, provider, a, d_addresses=("system:payments", "code:src/payments/retry.py"),
                       o_addresses=("code:src/payments/retry.py", "file:Dockerfile"))
        first = promote_if_supported(s, provider, a, propose(s, provider, a, d, o))
        v1 = _version_content(s, provider, a, first.object_id, 1)
        d2, o2 = episode(s, provider, a, trusted=False, o_addresses=("cluster:eu-west",))
        promote_if_supported(s, provider, a, propose(s, provider, a, d2, o2))
        v2 = _version_content(s, provider, a, first.object_id, 2)
    assert v1["addresses"] == ["code:src/payments/retry.py", "file:Dockerfile", "system:payments"]
    assert v2["addresses"] == ["cluster:eu-west", "code:src/payments/retry.py", "file:Dockerfile", "system:payments"]


def test_addresses_a_version_from_sources_without_addresses_has_no_addresses_key(rw, provider, streams):
    a = streams["a"]
    with rw() as s:
        b = grounded_belief(s, provider, a)
        assert "addresses" not in _version_content(s, provider, a, b.object_id, 1)


def test_d0020_am1_action_linked_outcomes_count_their_decisions_toward_the_quorum(rw, provider, streams):
    a = streams["a"]
    with rw() as s:
        d1, _, o1 = action_episode(s, provider, a)
        d2, _, o2 = action_episode(s, provider, a)
        p1 = promote_if_supported(s, provider, a, propose(s, provider, a, d1, o1))
        p2 = promote_if_supported(s, provider, a, propose(s, provider, a, d2, o2))
        (h,) = read_heads(s, provider, a)
    assert (p1.support, p2.support, p2.version) == ("single_source", "quorum", 2)
    assert h.content["support_decisions"] == sorted([str(d1), str(d2)])
