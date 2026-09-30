"""Tests for ledger/validate_append.py (D-0002, D-0012): validation and trust, no database needed."""
import uuid
from datetime import UTC, datetime

import pytest

from nacre.core.event import ActorKind, EventType, PayloadType, Source, TimeBasis, TimePrecision, Trust
from nacre.ledger.validate_append import AppendError, AppendRequest, Authorship, validate_append


def req(**kw):
    base = dict(stream_id=uuid.uuid4(), event_type=EventType.MESSAGE, payload_type=PayloadType.TEXT,
                actor_kind=ActorKind.AGENT, actor_id=uuid.uuid4(), source=Source.CHAT,
                idempotency_key=str(uuid.uuid4()), content="x")
    base.update(kw)
    return AppendRequest(**base)


@pytest.mark.parametrize("source,authorship,payload,expected", [
    (Source.CHAT, Authorship.EXTERNAL, PayloadType.TEXT, Trust.UNTRUSTED),
    (Source.CHAT, Authorship.SCOPE_PRINCIPAL, PayloadType.TEXT, Trust.TRUSTED),
    (Source.CI, Authorship.INTEGRATION_RESULT, PayloadType.STRUCTURED, Trust.TRUSTED),
    (Source.CI, Authorship.SCOPE_PRINCIPAL, PayloadType.TEXT, Trust.UNTRUSTED),
    (Source.TOOL, Authorship.INTEGRATION_RESULT, PayloadType.STRUCTURED, Trust.UNTRUSTED),
    (Source.WEB, Authorship.SCOPE_PRINCIPAL, PayloadType.TEXT, Trust.UNTRUSTED),
])
def test_trust_table(source, authorship, payload, expected):
    assert validate_append(req(source=source, authorship=authorship, payload_type=payload)) == expected


@pytest.mark.parametrize("kw,match", [
    (dict(source=Source.CI, authorship=Authorship.INTEGRATION_RESULT), "integration_result"),
    (dict(occurred_at=datetime(2026, 9, 30, 12, 0, 1, tzinfo=UTC), occurred_at_basis=TimeBasis.ASSERTED,
          occurred_at_precision=TimePrecision.MINUTE), "finer than"),
    (dict(occurred_at=datetime(2026, 9, 30, tzinfo=UTC), occurred_at_basis=TimeBasis.OBSERVED,
          occurred_at_precision=TimePrecision.DAY), "observed"),
    (dict(idempotency_key=str(uuid.uuid1())), "idempotency_key"),
    (dict(attachment=b"x"), "media type"),
    (dict(attachment_description="orphan metadata"), "without an attachment"),
])
def test_rejections(kw, match):
    with pytest.raises(AppendError, match=match):
        validate_append(req(**kw))


def test_append_event_re_exports_the_request_types():
    from nacre.ledger import append_event as ae
    assert ae.AppendRequest is AppendRequest and ae.Authorship is Authorship and ae.AppendError is AppendError
