"""Capture fixtures: a principal with read+append on stream a (and read on b)."""
import uuid

import pytest


@pytest.fixture
def rw(session, streams):
    p = uuid.uuid4()
    return lambda: session(p, read=[streams["a"], streams["b"]], write=[streams["a"]])
