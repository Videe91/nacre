"""Tests for sleep/propose_propositions.py and sleep/repair_structure.py: pinned prompts, request shape, parsing."""
import hashlib
import uuid

from sleep_kit import MODEL, Fake, crash, props
from nacre.eval.load_mnexa_family import load_mnexa_family
from nacre.sleep import propose_propositions as pp
from nacre.sleep import repair_structure as rs
from nacre.sleep.build_evidence_bundle import build_evidence_bundle

# Pinned on purpose: a prompt change must be deliberate and recorded (D-0020 / D1).
PROPOSER_SHA = hashlib.sha256(pp.PROPOSER_PROMPT.encode()).hexdigest()
REPAIR_SHA = hashlib.sha256(rs.REPAIR_PROMPT.encode()).hexdigest()


def test_prompts_are_pinned():
    assert pp.PROMPT_SHA256 == PROPOSER_SHA == "cb60f7717d46fca78fdca24c69dd8faf76d9c2b013ab9b46f8a267303b93e31b"
    assert rs.PROMPT_SHA256 == REPAIR_SHA == "ccb97d608856ccbc1def462ede3d61d94689423a62ef47cf3bb628796a095018"


def _bundle(world, provider, family):
    with world["session"]() as s:
        lf = load_mnexa_family(s, provider, world["proj"], family, 14)
        return build_evidence_bundle(s, provider, world["proj"], lf.outcome_id)


def test_the_request_is_pinned_deterministic_and_schema_bound(world, provider, family):
    b = _bundle(world, provider, family)
    fake = Fake([props((2, b.sections[2].text, b.sections[2].text[:10]))])
    with world["session"]() as s:
        out = pp.propose_propositions(s, provider, fake, b, run_id=uuid.uuid4())
    (r,) = fake.requests
    assert (r.model, r.params.temperature, r.params.response_format["strict"]) == (MODEL, 0.0, True)
    assert r.system == pp.PROPOSER_PROMPT and r.messages[0].content == b.render()
    assert out.parse_error is None and out.proposals[0].section == 2 and out.call.event_id is not None


def test_unparseable_or_failed_calls_yield_no_proposals(world, provider, family):
    b = _bundle(world, provider, family)
    with world["session"]() as s:
        bad = pp.propose_propositions(s, provider, Fake(["not json"]), b, run_id=uuid.uuid4())
        err = pp.propose_propositions(s, provider, Fake([crash()]), b, run_id=uuid.uuid4())
    assert (bad.proposals, bad.parse_error) == ([], "JSONDecodeError")
    assert err.proposals == [] and "APIConnectionError" in err.parse_error


def test_repair_sees_the_initial_list_and_is_skipped_when_empty(world, provider, family):
    b = _bundle(world, provider, family)
    q = b.sections[2].text
    first, _ = pp.parse_propositions(props((2, q, q[:8])))
    fake = Fake([props((2, q, q[:12]))])
    with world["session"]() as s:
        out = rs.repair_structure(s, provider, fake, b, first, run_id=uuid.uuid4())
        assert rs.repair_structure(s, provider, Fake([]), b, [], run_id=uuid.uuid4()) is None
    assert "PROPOSED PROPOSITIONS" in fake.requests[0].messages[0].content and out.proposals[0].nucleus == q[:12]
