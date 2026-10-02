"""Tests for capture/record_decision.py (D-0018)."""
import pytest

from capture_kit import decision
from nacre.capture.record_decision import CaptureError
from nacre.core.event import EventType
from nacre.ledger.read_stream import read_stream


def test_a_decision_is_recorded_with_its_body(rw, provider, streams):
    with rw() as s:
        d = decision(s, provider, streams["a"], stakes=("production",))
        (e,) = [x for x in read_stream(s, provider, streams["a"]) if x.envelope.event_id == d.event_id]
    c = e.body["content"]
    assert d.event_type == EventType.DECISION and c["reasoning_owner"] == "external" and c["stakes"] == ["production"]
    assert c["decided_from"] is None and c["refs"] == []


def test_decided_from_must_be_an_earlier_event_of_the_stream(rw, provider, streams):
    with rw() as s:
        first = decision(s, provider, streams["a"])
        second = decision(s, provider, streams["a"], decided_from=first.event_id, context_evidence_sha256="a" * 64)
        assert second.caused_by == first.event_id
        with pytest.raises(CaptureError, match="committed event"):
            decision(s, provider, streams["a"], decided_from=__import__("uuid").uuid4())


@pytest.mark.parametrize("kw", [{"text": " "}, {"stakes": ("reputation",)}, {"stakes": ("money", "money")},
                                {"context_evidence_sha256": "a" * 64}])
def test_invalid_decisions_are_refused(rw, provider, streams, kw):
    with rw() as s, pytest.raises(CaptureError):
        decision(s, provider, streams["a"], **kw)


# --- addresses (D-0025 §3, the D-0018 amendment) ---------------------------------------------------------------------

def test_addresses_are_stored_sorted_in_the_encrypted_body(rw, provider, streams):
    with rw() as s:
        d = decision(s, provider, streams["a"], addresses=("system:payments", "code:src/payments/retry.py"))
        (e,) = [x for x in read_stream(s, provider, streams["a"]) if x.envelope.event_id == d.event_id]
        raw = bytes(s.conn.execute("SELECT body_ciphertext FROM ledger.events WHERE event_id = %s",
                                 (d.event_id,)).fetchone()[0])
    assert e.body["content"]["addresses"] == ["code:src/payments/retry.py", "system:payments"]
    assert b"src/payments/retry.py" not in raw                     # content: encrypted with the event


def test_no_addresses_means_no_addresses_key(rw, provider, streams):
    with rw() as s:
        d = decision(s, provider, streams["a"])
        (e,) = [x for x in read_stream(s, provider, streams["a"]) if x.envelope.event_id == d.event_id]
    assert "addresses" not in e.body["content"]


@pytest.mark.parametrize("addresses", [
    ("payments",), ("code:",), ("Code:src/x.py",), ("service:payments",), (" code:x",), ("code: x",), ("code:x ",),
    ("code:a\nb",), ("code:" + "x" * 252,), ("system:a", "system:a"), tuple(f"entity:e{i}" for i in range(65)),
    "code:src/x.py", (1,), [["code:x"]]])
def test_invalid_addresses_are_refused_never_dropped(rw, provider, streams, addresses):
    with rw() as s, pytest.raises(CaptureError):
        decision(s, provider, streams["a"], addresses=addresses)


def test_address_limits_are_inclusive():
    from nacre.capture.record_decision import MAX_ADDRESS_CHARS, MAX_ADDRESSES, check_addresses
    longest = "code:" + "x" * (MAX_ADDRESS_CHARS - 5)
    assert check_addresses((longest,)) == {"addresses": [longest]}
    many = tuple(f"entity:e{i:02d}" for i in range(MAX_ADDRESSES))
    assert check_addresses(many) == {"addresses": sorted(many)} and check_addresses(()) == {}
    for t in ("file", "code", "entity", "system", "cluster", "domain"):
        assert check_addresses([f"{t}:v w"]) == {"addresses": [f"{t}:v w"]}


# Golden: SHA-256 of the CBOR body each record_* produced at commit ad54f31 (before addresses existed), for a fixed
# input. An event without addresses must encode byte-identically.
GOLDEN = {
    "record_decision": "69172b7cb47c031ac33e22a78f6ae1e58d0422330d1e3ceb982b57cacee8c4ee",
    "record_prediction": "71928105937802a22ab4ce29c6663eb1d5581277bdbc1ae0c0a505ecee317325",
    "record_action": "d13a949897a12a9cff6428d522b4d11168a01d53d38b800bb6cb2972fd6e04a4",
    "record_outcome": "d604d68095d26b888ecae148e8f051cf0e2ab7e93a04495313b5cbcc828e5f1d",
    "record_correction": "8c4bf1f9b0e65f22bc8f1f8f6b372ff46503ef3c2abe685b1f819734a98fb1ff",
}


class _Rows:
    def fetchone(self):
        return ("decision",)


class _Session:
    class conn:                                                     # noqa: N801 - mimics ScopedSession.conn
        @staticmethod
        def execute(*_a):
            return _Rows()


def _bodies(monkeypatch, **extra):
    import hashlib
    import importlib
    import uuid as _uuid

    from nacre.capture.record_outcome import Section
    from nacre.core.encode_cbor import encode_cbor
    from nacre.core.event import ActorKind, Source
    from nacre.ledger.append_event import Authorship
    u = lambda n: _uuid.UUID(int=n)                                 # noqa: E731
    who = dict(stream_id=u(1), actor_kind=ActorKind.AGENT, actor_id=u(2), source=Source.CHAT,
               authorship=Authorship.SCOPE_PRINCIPAL, idempotency_key="k", **extra)
    calls = {"record_decision": dict(decision_text="ship it", stakes=("money",)),
             "record_prediction": dict(decision_id=u(3), expected_outcome="green", expected_success=True,
                                       confidence_pct=70),
             "record_action": dict(decision_id=u(3), action_kind="edit", description="patch retry"),
             "record_outcome": dict(outcome_for=u(3), success=False, sections=(Section("correction", "use backoff"),),
                                    failing_checks=("t1",)),
             "record_correction": dict(correction_of=u(3), text="no, use jitter")}
    out = {}
    for name, kw in calls.items():
        m, seen = importlib.import_module(f"nacre.capture.{name}"), []
        monkeypatch.setattr(m, "append_event", lambda s, kp, verified=None, request=None: seen.append(request))
        monkeypatch.setattr(m, "validate_refs", lambda s, st, refs: [{"rel": r.rel, "event_id": str(r.event_id)}
                                                                     for r in refs])
        getattr(m, name)(_Session(), None, **who, **kw)
        out[name] = (hashlib.sha256(encode_cbor(seen[0].content)).hexdigest(), seen[0].content)
    return out


def test_golden_an_event_without_addresses_encodes_byte_identically_to_before(monkeypatch):
    assert {k: v[0] for k, v in _bodies(monkeypatch).items()} == GOLDEN
    assert {k: v[0] for k, v in _bodies(monkeypatch, addresses=()).items()} == GOLDEN


def test_every_record_function_accepts_and_stores_addresses(monkeypatch):
    got = _bodies(monkeypatch, addresses=("system:payments", "code:src/payments/retry.py"))
    for name, (digest, content) in got.items():
        assert content["addresses"] == ["code:src/payments/retry.py", "system:payments"], name
        assert digest != GOLDEN[name]
    with pytest.raises(CaptureError):
        _bodies(monkeypatch, addresses=("bogus:x",))
