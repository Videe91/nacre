"""D-0023 contributor-set keys: the tests the ADR names (Phase 2 gate item 9). Erasure runs through the real D-0014
lifecycle (request, 7-day grace, execution)."""
import ast
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import psycopg
import pytest

from nacre.capture.record_decision import record_decision
from nacre.capture.record_outcome import Section, record_outcome
from nacre.core.event import ActorKind, EventType, PayloadType, Source
from nacre.core.model_provider import Message, ModelParams, ModelRequest, ModelResponse, Usage
from nacre.keys.derive_contributor_key import (MAX_CONTRIBUTORS, Contributor, ContributorCapExceeded, contributors_of,
                                               derived_key)
from nacre.keys.execute_due_shreds import execute_due_shreds
from nacre.keys.keyadmin_session import keyadmin_transaction
from nacre.keys.manage_shred_requests import ShredKind, place_legal_hold, request_shred
from nacre.keys.rotate_master_key import rotate_master_key
from nacre.ledger.append_event import AppendError, AppendRequest, Authorship, append_event
from nacre.ledger.read_stream import read_stream
from nacre.models.call_model import ModelCallRefused, call_model
from nacre.models.set_model_policy import set_model_policy
from nacre.scopes.register_scope import ScopeKind, register_scope
from nacre.scopes.set_access import set_access
from nacre.stores.promote_if_supported import promote_if_supported
from nacre.stores.propose_lesson import propose_lesson
from nacre.stores.read_heads import read_heads
from nacre.stores.rebuild_projection import rebuild_projection

MODEL = "gpt-4o-mini-2024-07-18"
NOW = datetime.now(UTC)
ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Echo:
    name: str = "openai"
    replay: bool = False
    calls: list = field(default_factory=list)

    def complete(self, request, *, timeout_s):
        self.calls.append(request)
        return ModelResponse("noted: " + request.messages[0].content, "completed", Usage(10, 5, None), "r", MODEL, 1)


@pytest.fixture
def w(org, provider, migrated_db):
    org_id, owner, open_ = org
    proj = uuid.uuid4()
    with open_(owner) as s:
        register_scope(s, provider, org_id=org_id, stream_id=proj, kind=ScopeKind.PROJECT, idempotency_key=str(uuid.uuid4()))
    with open_(owner) as s:
        set_access(s, provider, org_id=org_id, principal_id=owner, stream_id=proj, can_read=True, can_append=True,
                   idempotency_key=str(uuid.uuid4()))
    with open_(owner) as s:
        set_model_policy(s, provider, org_id=org_id, allowed=[("openai", MODEL)], idempotency_key=str(uuid.uuid4()))
    P, Q = uuid.uuid4(), uuid.uuid4()

    def say(person, text, etype=EventType.STATEMENT, **kw):
        with open_(owner) as s:
            return append_event(s, provider, AppendRequest(
                stream_id=proj, event_type=etype, payload_type=PayloadType.TEXT, actor_kind=kw.pop("actor_kind", ActorKind.PERSON),
                actor_id=person, source=Source.CHAT, authorship=Authorship.SCOPE_PRINCIPAL,
                idempotency_key=str(uuid.uuid4()), content=text, **kw)).envelope

    def model(sources, text="summarise"):
        with open_(owner) as s:
            return call_model(s, provider, Echo(), ModelRequest("openai", MODEL, (Message("user", text),), ModelParams(max_tokens=64), "t"),
                              source_event_ids=list(sources), run_id=uuid.uuid4()).event_id

    def readable(event_id):
        with open_(owner) as s:
            (e,) = [x for x in read_stream(s, provider, proj) if x.envelope.event_id == event_id]
        return isinstance(e.body, dict)

    def erase(person, *, hold_until=None, run_at=NOW + timedelta(days=8), requester=None):
        ka = lambda: psycopg.connect(migrated_db["keyadmin"])  # noqa: E731
        with ka() as c, keyadmin_transaction(c) as tx:
            rid = request_shred(tx, provider, org_id=org_id, requester=requester or owner, kind=ShredKind.ERASE_PERSON,
                                person_id=person, idempotency_key=str(uuid.uuid4()), now=NOW)
        if hold_until:
            with ka() as c, keyadmin_transaction(c) as tx:
                place_legal_hold(tx, provider, org_id=org_id, requester=owner, request_id=rid, until=hold_until,
                                 reason="litigation", idempotency_key=str(uuid.uuid4()), now=NOW)
        execute_due_shreds(ka(), provider, now=run_at)
        return rid

    def forget(months):
        ka = lambda: psycopg.connect(migrated_db["keyadmin"])  # noqa: E731
        with ka() as c, keyadmin_transaction(c) as tx:
            request_shred(tx, provider, org_id=org_id, requester=owner, kind=ShredKind.FORGET_PERIOD, stream_id=proj,
                          months=tuple(months), idempotency_key=str(uuid.uuid4()), now=NOW)
        execute_due_shreds(ka(), provider, now=NOW + timedelta(days=8))
    return dict(org=org_id, owner=owner, open=open_, proj=proj, P=P, Q=Q, say=say, model=model, readable=readable,
                erase=erase, forget=forget, ka=lambda: psycopg.connect(migrated_db["keyadmin"]), admin=migrated_db["admin"])


def test_1_erasure_reaches_every_derived_record_that_touched_the_person_and_only_those(w, provider):
    p_said, q_said = w["say"](w["P"], "P's private plan"), w["say"](w["Q"], "Q's private plan")
    sys_ev = w["say"](w["owner"], "build 42 green", EventType.RESULT, actor_kind=ActorKind.TOOL)
    rec_p, rec_q, rec_s = w["model"]([p_said.event_id]), w["model"]([q_said.event_id]), w["model"]([sys_ev.event_id])
    rec_of_rec_p = w["model"]([rec_p], "summarise the summary")                     # derived from derived: still P
    w["erase"](w["P"])
    assert [w["readable"](x) for x in (p_said.event_id, rec_p, rec_of_rec_p)] == [False, False, False]
    assert [w["readable"](x) for x in (q_said.event_id, sys_ev.event_id, rec_q, rec_s)] == [True, True, True, True]


@pytest.mark.parametrize("who", ["P", "Q"])
def test_2_mixed_contributor_records_are_erased_whole(w, provider, who):
    p_said, q_said = w["say"](w["P"], "P part"), w["say"](w["Q"], "Q part")
    mixed = w["model"]([p_said.event_id, q_said.event_id])
    w["erase"](w[who])
    other = q_said if who == "P" else p_said
    assert not w["readable"](mixed) and w["readable"](other.event_id)


def _person_belief(w, provider):
    """A belief grounded in a correction AUTHORED by person P (trusted chat, person actor)."""
    with w["open"](w["owner"]) as s:
        d = record_decision(s, provider, stream_id=w["proj"], actor_kind=ActorKind.AGENT, actor_id=uuid.uuid4(),
                            source=Source.CHAT, authorship=Authorship.SCOPE_PRINCIPAL, idempotency_key=str(uuid.uuid4()),
                            decision_text="retry now").envelope
        rule = "Wait 30 seconds before retrying the export."
        o = record_outcome(s, provider, stream_id=w["proj"], actor_kind=ActorKind.PERSON, actor_id=w["P"], source=Source.CHAT,
                           authorship=Authorship.SCOPE_PRINCIPAL, idempotency_key=str(uuid.uuid4()), outcome_for=d.event_id,
                           success=False, sections=(Section("correction", rule),)).envelope
        pid = propose_lesson(s, provider, stream_id=w["proj"], decision_id=d.event_id, outcome_id=o.event_id, section_index=0,
                             span=(0, len(rule)), nucleus="Wait 30 seconds").event_id
        return promote_if_supported(s, provider, w["proj"], pid)


def test_3_a_belief_from_an_erased_persons_content_is_lost_and_rebuild_treats_it_as_shredded(w, provider):
    b = _person_belief(w, provider)
    assert b is not None and b.support == "single_source"
    w["erase"](w["P"])
    with w["open"](w["owner"]) as s:
        assert read_heads(s, provider, w["proj"]) == []                                       # not recall-eligible
        (h,) = read_heads(s, provider, w["proj"], include_unreadable=True)
        check = rebuild_projection(s, provider, w["proj"], switch=False)
    assert (h.object_id, h.content) == (b.object_id, None)
    assert check.identical and check.unverifiable                                              # shredded, not a difference


def test_3b_a_legal_hold_keeps_the_belief_until_it_expires(w, provider):
    b = _person_belief(w, provider)
    w["erase"](w["P"], requester=w["P"], hold_until=NOW + timedelta(days=30), run_at=NOW + timedelta(days=8))   # self-erasure, held
    with w["open"](w["owner"]) as s:
        assert [h.object_id for h in read_heads(s, provider, w["proj"])] == [b.object_id]       # held: still readable
    execute_due_shreds(w["ka"](), provider, now=NOW + timedelta(days=31))                      # hold expired: executes
    with w["open"](w["owner"]) as s:
        assert read_heads(s, provider, w["proj"]) == []


def test_4_forgetting_a_month_destroys_derived_keys_with_membership_in_it(w, provider):
    aug, sep = date(2026, 8, 1), date(2026, 9, 1)
    with w["open"](w["owner"]) as s:
        system = w["proj"]
        k_aug = derived_key(s.conn, provider, w["proj"], frozenset({Contributor(system, aug, False), Contributor(w["P"], sep, True)}), sep)
        k_sep = derived_key(s.conn, provider, w["proj"], frozenset({Contributor(system, sep, False)}), sep)
    w["forget"]([aug])
    with psycopg.connect(w["admin"]) as c:
        alive = {k for (k,) in c.execute("SELECT key_id FROM keys.data_keys WHERE key_id = ANY(%s)", ([k_aug.key_id, k_sep.key_id],))}
        members = c.execute("SELECT count(*) FROM keys.key_contributors WHERE key_id = %s", (k_aug.key_id,)).fetchone()[0]
    assert alive == {k_sep.key_id} and members == 0                                          # membership cascades


@pytest.mark.parametrize("etype", [EventType.CORRECTION, EventType.DECISION, EventType.PREDICTION, EventType.OUTCOME,
                                   EventType.STATEMENT])
def test_5_every_event_authored_by_a_person_is_under_their_key_and_erased_with_them(w, provider, etype):
    extra = {"caused_by": w["say"](w["Q"], "earlier").event_id} if etype == EventType.CORRECTION else {}
    ev = w["say"](w["P"], "my words", etype, **extra)
    with psycopg.connect(w["admin"]) as c:
        assert c.execute("SELECT subject_id FROM keys.data_keys WHERE key_id = %s", (ev.key_id,)).fetchone()[0] == w["P"]
    w["erase"](w["P"])
    assert not w["readable"](ev.event_id)


def test_5b_an_agent_acting_on_a_persons_behalf_uses_their_key(w, provider):
    ev = w["say"](uuid.uuid4(), "booked the flight for P", EventType.ACTION, actor_kind=ActorKind.AGENT, on_behalf_of=w["P"])
    w["erase"](w["P"])
    assert not w["readable"](ev.event_id)
    with pytest.raises(AppendError, match="on_behalf_of"):
        w["say"](w["Q"], "x", on_behalf_of=w["P"])                                             # persons speak for themselves


def test_6_rotation_rewraps_derived_keys_and_records_stay_readable(w, provider):
    rec = w["model"]([w["say"](w["P"], "P text").event_id])
    with w["ka"]() as c, keyadmin_transaction(c) as tx:
        assert rotate_master_key(tx, provider, org_id=w["org"], stream_id=w["proj"], operator=w["owner"]) >= 1
    assert w["readable"](rec)


def test_6b_key_membership_is_scoped_like_data_keys(w, provider):
    w["model"]([w["say"](w["P"], "P text").event_id])
    with w["open"](uuid.uuid4()) as s:                                                         # no grants
        assert s.conn.execute("SELECT count(*) FROM keys.key_contributors").fetchone()[0] == 0
    with w["open"](w["owner"]) as s:
        assert s.conn.execute("SELECT count(*) FROM keys.key_contributors").fetchone()[0] >= 2


def test_cap_fails_closed_and_never_falls_back(w, provider, monkeypatch):
    many = frozenset(Contributor(uuid.uuid4(), date(2026, 9, 1), True) for _ in range(MAX_CONTRIBUTORS + 1))
    with w["open"](w["owner"]) as s:
        before = s.conn.execute("SELECT count(*) FROM keys.data_keys").fetchone()[0]
        with pytest.raises(ContributorCapExceeded):
            derived_key(s.conn, provider, w["proj"], many, date(2026, 9, 1))
        assert s.conn.execute("SELECT count(*) FROM keys.data_keys").fetchone()[0] == before
    from nacre.models import call_model as cm
    monkeypatch.setattr(cm, "MAX_CONTRIBUTORS", 1)
    echo = Echo()
    with w["open"](w["owner"]) as s, pytest.raises(ModelCallRefused, match="D-0023"):
        call_model(s, provider, echo, ModelRequest("openai", MODEL, (Message("user", "x"),), ModelParams(max_tokens=8), "t"),
                   source_event_ids=[w["say"](w["P"], "p").event_id], run_id=uuid.uuid4())
    assert echo.calls == []                                                                    # refused before paying


def test_shredded_content_cannot_be_derived_from(w, provider):
    said = w["say"](w["P"], "P text")
    w["erase"](w["P"])
    with w["open"](w["owner"]) as s:
        with pytest.raises(ModelCallRefused, match="D-0023"):
            call_model(s, provider, Echo(), ModelRequest("openai", MODEL, (Message("user", "x"),), ModelParams(max_tokens=8), "t"),
                       source_event_ids=[said.event_id], run_id=uuid.uuid4())
        with pytest.raises(AppendError, match="derived write refused"):
            append_event(s, provider, AppendRequest(
                stream_id=w["proj"], event_type=EventType.MEMORY_EVENT, payload_type=PayloadType.STRUCTURED,
                actor_kind=ActorKind.SYSTEM, actor_id=uuid.uuid4(), source=Source.SYSTEM, authorship=Authorship.SCOPE_PRINCIPAL,
                idempotency_key=str(uuid.uuid4()), content={"op": "x"}, sources=(said.event_id,)))
        assert contributors_of(s.conn, w["proj"], []) == frozenset()


def test_7_every_product_memory_or_model_result_append_names_its_sources():
    """No derived write falls back to the stream key: each AppendRequest for a memory_event or a model result in product
    code passes `sources=` (content-free run markers pass it too, possibly empty, through sleep's _marker)."""
    offenders = []
    for path in (ROOT / "src" / "nacre").rglob("*.py"):
        if "eval" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "AppendRequest":
                kws = {k.arg: ast.unparse(k.value) for k in node.keywords}
                derived = kws.get("event_type") == "EventType.MEMORY_EVENT" or (
                    kws.get("event_type") == "EventType.RESULT" and kws.get("actor_kind") == "ActorKind.MODEL")
                if derived and "sources" not in kws:
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert offenders == []
