"""Signed state: round trip, tamper rejection, expiry, type confusion."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.security import create_access_token
from app.integrations import oauth
from app.integrations.oauth import IntegrationAuthError, mint_state, verify_state


def _mint(**over):
    defaults = dict(
        workspace_id="ws-1",
        provider="google",
        user_id="user-1",
        return_to="/settings/integrations",
    )
    defaults.update(over)
    return mint_state(**defaults)


def test_state_round_trip():
    state = _mint(connection_id="conn-9", return_to="/settings/channels")
    claims = verify_state(state)
    assert claims["ws"] == "ws-1"
    assert claims["provider"] == "google"
    assert claims["sub"] == "user-1"
    assert claims["return_to"] == "/settings/channels"
    assert claims["connection_id"] == "conn-9"
    assert claims["purpose"] == "integrations.connect"
    assert claims["nonce"]


def test_state_omits_connection_id_by_default():
    claims = verify_state(_mint())
    assert "connection_id" not in claims


def test_tampered_state_rejected():
    state = _mint()
    # Flip one character of the signature.
    tampered = state[:-2] + ("A" if state[-2] != "A" else "B") + state[-1]
    with pytest.raises(IntegrationAuthError):
        verify_state(tampered)


def test_garbage_state_rejected():
    with pytest.raises(IntegrationAuthError):
        verify_state("not-a-jwt")


def test_expired_state_rejected(monkeypatch):
    monkeypatch.setattr(oauth, "STATE_TTL", timedelta(seconds=-10))
    state = _mint()
    with pytest.raises(IntegrationAuthError):
        verify_state(state)


def test_access_token_is_not_a_valid_state():
    """Type confusion guard: a real user access JWT must never pass as state."""
    with pytest.raises(IntegrationAuthError):
        verify_state(create_access_token("user-1"))
