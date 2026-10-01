"""Eval fixtures: block the network (recorded mode must never call out, SI-6)."""
import socket

import pytest


@pytest.fixture
def no_network(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("network access attempted in recorded mode (SI-6)")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
