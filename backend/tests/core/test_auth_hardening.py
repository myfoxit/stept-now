"""Password-reset single-use semantics and the cookie-route origin check."""

from __future__ import annotations

from tests.conftest import bearer, signup


async def _request_reset(client, monkeypatch, email: str) -> str:
    captured: dict = {}

    async def fake_send(to, subject, html, **kwargs):
        captured["html"] = html
        return True

    monkeypatch.setattr("app.api.v1.auth.send_email", fake_send)
    response = await client.post("/api/v1/auth/password-reset", json={"email": email})
    assert response.status_code == 200
    return captured["html"].split("token=")[1].split('"')[0]


async def test_reset_token_is_single_use(client, monkeypatch):
    await signup(client, "once@example.com")
    token = await _request_reset(client, monkeypatch, "once@example.com")

    first = await client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": token, "new_password": "first-new-pass-1"},
    )
    assert first.status_code == 200

    # The whole point: the same link must not work a second time, even though
    # it is still inside its one-hour window.
    replay = await client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": token, "new_password": "attacker-pass-9"},
    )
    assert replay.status_code == 401

    # ...and the password the replay tried to set was never applied.
    assert (
        await client.post(
            "/api/v1/auth/login",
            json={"email": "once@example.com", "password": "attacker-pass-9"},
        )
    ).status_code == 401
    assert (
        await client.post(
            "/api/v1/auth/login",
            json={"email": "once@example.com", "password": "first-new-pass-1"},
        )
    ).status_code == 200


async def test_requesting_a_second_reset_invalidates_the_first(client, monkeypatch):
    await signup(client, "second@example.com")
    first_token = await _request_reset(client, monkeypatch, "second@example.com")
    second_token = await _request_reset(client, monkeypatch, "second@example.com")
    assert first_token != second_token

    stale = await client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": first_token, "new_password": "stale-link-pass-1"},
    )
    assert stale.status_code == 401

    fresh = await client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": second_token, "new_password": "fresh-link-pass-1"},
    )
    assert fresh.status_code == 200


async def test_changing_password_kills_outstanding_reset_link(client, monkeypatch):
    auth = await signup(client, "changed@example.com")
    token = await _request_reset(client, monkeypatch, "changed@example.com")

    changed = await client.post(
        "/api/v1/me/change-password",
        json={"current_password": "password-123", "new_password": "chosen-by-me-1"},
        headers=bearer(auth),
    )
    assert changed.status_code == 200

    # The link was mailed before the change; letting it still work would hand an
    # old inbox the ability to undo a deliberate password change.
    hijack = await client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": token, "new_password": "hijacked-pass-1"},
    )
    assert hijack.status_code == 401


async def test_unknown_reset_token_rejected(client):
    response = await client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": "not-a-real-token", "new_password": "whatever-pass-1"},
    )
    assert response.status_code == 401


async def test_reset_for_unknown_email_still_reports_success(client, monkeypatch):
    sent: dict = {}

    async def fake_send(to, subject, html, **kwargs):
        sent["to"] = to
        return True

    monkeypatch.setattr("app.api.v1.auth.send_email", fake_send)
    response = await client.post(
        "/api/v1/auth/password-reset", json={"email": "nobody@example.com"}
    )
    assert response.status_code == 200
    assert "to" not in sent  # nothing mailed, but the caller cannot tell


async def test_refresh_rejects_foreign_origin(client):
    auth = await signup(client, "csrf@example.com")
    assert auth  # cookie is on the client

    blocked = await client.post("/api/v1/auth/refresh", headers={"origin": "https://evil.example"})
    assert blocked.status_code == 403

    # Same flow without the hostile header still works, so the guard is not
    # simply breaking refresh for everyone.
    allowed = await client.post("/api/v1/auth/refresh")
    assert allowed.status_code == 200


async def test_logout_rejects_foreign_origin(client):
    await signup(client, "csrf-logout@example.com")
    blocked = await client.post("/api/v1/auth/logout", headers={"origin": "https://evil.example"})
    assert blocked.status_code == 403


async def test_configured_app_origin_is_accepted(client):
    from app.core.config import get_settings

    await signup(client, "good-origin@example.com")
    response = await client.post(
        "/api/v1/auth/refresh", headers={"origin": get_settings().app_base_url}
    )
    assert response.status_code == 200


async def test_login_for_unknown_account_is_generic(client):
    """No account-existence oracle in the message (the timing side is covered by
    burning an argon2 verify; asserting on wall-clock here would be flaky)."""
    response = await client.post(
        "/api/v1/auth/login", json={"email": "ghost@example.com", "password": "whatever-123"}
    )
    assert response.status_code == 401
    assert response.json()["error"]["message"] == "Invalid email or password"
