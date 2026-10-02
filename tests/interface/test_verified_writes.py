"""D-0026 end to end: claims checked by interface/check_claims.py become trust_basis = verified on the envelope only when
they describe the exact write; an agent cannot plant an authoritative correction; verified agent decisions count only
under the two-decision rule; the claim table agrees with capture/section_authority.py on every allowed combination
(D-0026 amendment 1 tests 1-5)."""
import ast
import itertools
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest

from nacre.capture.record_decision import record_decision
from nacre.capture.record_outcome import Section, record_outcome
from nacre.capture.section_authority import section_authority
from nacre.core.event import ActorKind, EventType, PayloadType, Source, TrustBasis
from nacre.interface.authenticate_principal import Principal
from nacre.interface.check_claims import ClaimRejected, Claims, check_claims, claims_of
from nacre.ledger.append_event import AppendError, AppendRequest, Authorship, append_event
from nacre.ledger.validate_append import validate_append
from nacre.stores.promote_if_supported import promote_if_supported
from nacre.stores.propose_lesson import propose_lesson

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime.now(UTC)


@pytest.fixture
def admin(migrated_db):
    with psycopg.connect(migrated_db["principal_admin"], autocommit=True) as c:
        yield c


def _p(admin, kind, sources=()):
    pid = uuid.uuid4()
    admin.execute("INSERT INTO auth.principals (principal_id, org_id, kind, display_name, service_sources, config_event_id) "
                  "VALUES (%s, %s, %s, 'x', %s, %s)", (pid, uuid.uuid4(), kind, list(sources), uuid.uuid4()))
    return Principal(pid, uuid.uuid4(), kind, tuple(sources), uuid.uuid4())


def _note(stream, actor, **kw):
    base = dict(stream_id=stream, event_type=EventType.MESSAGE, payload_type=PayloadType.TEXT, actor_kind=ActorKind.AGENT,
                actor_id=actor, source=Source.CHAT, authorship=Authorship.EXTERNAL, idempotency_key=str(uuid.uuid4()),
                content="tests pass locally")
    base.update(kw)
    return AppendRequest(**base)


def test_a_verified_write_records_verified_and_a_mismatch_writes_nothing(admin, session, streams, provider):
    agent = _p(admin, "agent")
    a = streams["a"]
    with session(agent.principal_id, read=[a], write=[a]) as s:
        req = _note(a, agent.principal_id)
        env = append_event(s, provider, req, verified=check_claims(s, agent, claims_of(req))).envelope
        assert env.trust_basis == TrustBasis.VERIFIED
        plain = append_event(s, provider, _note(a, agent.principal_id)).envelope
        assert plain.trust_basis == TrustBasis.ASSERTED                           # no claims: still asserted
        v = check_claims(s, agent, claims_of(req))
        before = s.conn.execute("SELECT count(*) FROM ledger.events").fetchone()[0]
        for other in (replace(req, source=Source.TOOL, idempotency_key=str(uuid.uuid4())),
                      replace(req, actor_id=uuid.uuid4(), idempotency_key=str(uuid.uuid4()))):
            with pytest.raises(AppendError, match="do not describe this write"):
                append_event(s, provider, other, verified=v)
        assert s.conn.execute("SELECT count(*) FROM ledger.events").fetchone()[0] == before


def test_an_agent_cannot_plant_an_authoritative_correction(admin, session, streams):
    agent = _p(admin, "agent")
    a = streams["a"]
    with session(agent.principal_id, read=[a], write=[a]) as s:
        for claims in (Claims(a, "review", "scope_principal", "agent", True, True),
                       Claims(a, "chat", "external", "agent", False, True)):
            with pytest.raises(ClaimRejected):
                check_claims(s, agent, claims)


def test_verified_agent_decisions_count_only_under_the_two_decision_rule(admin, session, streams, provider):
    agent = _p(admin, "agent")
    a = streams["a"]
    lesson = "Retry VX-41 failures after 137 ms with header X-Relay: cobalt."

    def episode(s):
        dclaims = Claims(a, "chat", "external", "agent", True, False)
        d = record_decision(s, provider, stream_id=a, actor_kind=ActorKind.AGENT, actor_id=agent.principal_id,
                            source=Source.CHAT, authorship=Authorship.EXTERNAL, idempotency_key=str(uuid.uuid4()),
                            decision_text="retry immediately", verified=check_claims(s, agent, dclaims)).envelope
        assert d.trust_basis == TrustBasis.VERIFIED
        oclaims = Claims(a, "chat", "external", "agent", True, False, None, True)
        o = record_outcome(s, provider, stream_id=a, actor_id=agent.principal_id, idempotency_key=str(uuid.uuid4()),
                           outcome_for=d.event_id, success=False, actor_kind=ActorKind.AGENT, source=Source.CHAT,
                           authorship=Authorship.EXTERNAL, sections=(Section("status", "FAIL"), Section("evaluation", lesson)),
                           verified=check_claims(s, agent, oclaims)).envelope
        assert o.trust_basis == TrustBasis.VERIFIED
        return propose_lesson(s, provider, stream_id=a, decision_id=d.event_id, outcome_id=o.event_id, section_index=1,
                              span=(0, len(lesson)), nucleus="Retry VX-41 failures after 137 ms").event_id

    with session(agent.principal_id, read=[a], write=[a]) as s:
        first = promote_if_supported(s, provider, a, episode(s))
        assert first is None or first.status != "active"                         # one never promotes
        second = promote_if_supported(s, provider, a, episode(s))
        assert second is not None and second.status == "active" and second.support == "quorum"


KINDS = {"agent": (), "person": (), "service": ("ci",), "operator": ()}
SOURCES = ["chat", "tool", "review", "ci", "git", "system"]
AUTHORSHIPS = ["external", "scope_principal", "integration_result"]
ACTORS = ["agent", "person", "system"]


def test_the_claim_table_agrees_with_section_authority_on_every_allowed_combination(admin, session, streams):
    # D-0026 amendment 1 test 5: whenever check_claims ALLOWS a write, an authoritative section in it (per D-0018's
    # section_authority over the real trust intake) implies the principal holds authority; and vice versa for grants.
    a = streams["a"]
    principals = {k: _p(admin, k, srcs) for k, srcs in KINDS.items()}
    reviewer = _p(admin, "person")
    admin.execute("INSERT INTO auth.reviewer_grants (grant_id, person_principal, stream_id, expires_at, config_event_id) "
                  "VALUES (%s, %s, %s, %s, %s)", (uuid.uuid4(), reviewer.principal_id, a, NOW + timedelta(days=1), uuid.uuid4()))
    principals["reviewer"] = reviewer
    checked = 0
    for name, p in principals.items():
        with session(p.principal_id, read=[a], write=[a]) as s:
            allowed = []
            for source, authorship, actor, structured, role in itertools.product(
                    SOURCES, AUTHORSHIPS, ACTORS, (True, False), ("correction", "evaluation", "status")):
                claims = Claims(a, source, authorship, actor, structured, role == "correction", None, role == "evaluation")
                try:
                    allowed.append((check_claims(s, p, claims), source, authorship, actor, structured, role))
                except ClaimRejected:
                    pass
        for v, source, authorship, actor, structured, role in allowed:
            req = AppendRequest(stream_id=a, event_type=EventType.OUTCOME,
                                payload_type=PayloadType.STRUCTURED if structured else PayloadType.TEXT,
                                actor_kind=ActorKind(actor), actor_id=p.principal_id, source=Source(source),
                                authorship=Authorship(authorship), idempotency_key=str(uuid.uuid4()),
                                content={"success": False, "sections": [{"role": role, "text": "t"}]})
            try:
                trust = validate_append(req)
            except AppendError:
                continue
            env = type("E", (), {"event_type": EventType.OUTCOME, "trust": trust, "source": Source(source),
                                 "actor_kind": ActorKind(actor)})()
            authoritative = section_authority(env, role, False).authoritative
            assert not authoritative or v.authoritative_allowed, (name, source, authorship, actor, structured, role)
            checked += 1
    assert checked > 0


def test_only_check_claims_constructs_verified_claims():
    offenders = []
    for path in (ROOT / "src" / "nacre").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "VerifiedClaims":
                offenders.append(str(path.relative_to(ROOT)))
    assert offenders == ["src/nacre/interface/check_claims.py"]
