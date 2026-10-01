"""Recall fixtures: a principal with read+append on stream a (stream b is another scope)."""
import uuid

import pytest


@pytest.fixture
def rw(session, streams):
    p = uuid.uuid4()
    return lambda: session(p, read=[streams["a"]], write=[streams["a"]])
