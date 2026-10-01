"""Tests for models/set_model_policy.py (D-0021 provider policy, D3: default deny)."""
import uuid

import psycopg
import pytest

from model_fakes import MODEL
from nacre.models.set_model_policy import PolicyError, allowed_models, set_model_policy


def test_no_policy_means_nothing_is_allowed(world, provider):
    with world["open"](world["owner"]) as s:
        assert allowed_models(s, provider, world["org"]) == frozenset()


def test_the_latest_allow_list_replaces_the_previous_one(world, provider):
    world["allow"](MODEL, "gpt-4o-mini-2099-01-01")
    world["allow"](MODEL)
    with world["open"](world["owner"]) as s:
        assert allowed_models(s, provider, world["org"]) == {("openai", MODEL)}


@pytest.mark.parametrize("entry", [("openai", "gpt-4o-mini"), ("mystery", MODEL), ("openai", "GPT-4o-2024-08-06")])
def test_only_known_providers_and_dated_pins(world, provider, entry):
    with world["open"](world["owner"]) as s, pytest.raises(PolicyError):
        set_model_policy(s, provider, org_id=world["org"], allowed=[entry], idempotency_key=str(uuid.uuid4()))


def test_a_non_admin_cannot_set_it_and_cannot_read_it(world, provider):
    stranger = uuid.uuid4()
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with world["open"](stranger) as s:
            set_model_policy(s, provider, org_id=world["org"], allowed=[("openai", MODEL)], idempotency_key=str(uuid.uuid4()))
    world["allow"](MODEL)
    with world["open"](stranger) as s:
        assert allowed_models(s, provider, world["org"]) == frozenset()       # unreadable org stream = deny
