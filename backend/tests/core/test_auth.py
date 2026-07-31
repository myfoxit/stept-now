"""Auth flows: signup, login, refresh rotation + reuse detection, password reset."""

from tests.conftest import bearer, signup


async def test_signup_login_me(client):
    auth = await signup(client, "jane@example.com", name="Jane")
    assert auth["user"]["email"] == "jane@example.com"

    me = await client.get("/api/v1/me", headers=bearer(auth))
    assert me.status_code == 200
    assert me.json()["user"]["name"] == "Jane"
    assert me.json()["memberships"] == []

    login = await client.post(
        "/api/v1/auth/login", json={"email": "jane@example.com", "password": "password-123"}
    )
    assert login.status_code == 200


async def test_duplicate_signup_conflicts(client):
    await signup(client, "dup@example.com")
    response = await client.post(
        "/api/v1/auth/signup",
        json={"email": "dup@example.com", "name": "X", "password": "password-123"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


async def test_wrong_password_rejected(client):
    await signup(client, "amy@example.com")
    response = await client.post(
        "/api/v1/auth/login", json={"email": "amy@example.com", "password": "nope-nope-nope"}
    )
    assert response.status_code == 401


async def test_refresh_rotation_and_reuse_detection(client):
    await signup(client, "rot@example.com")
    # httpx keeps cookies; grab the current refresh cookie value.
    original_cookie = client.cookies.get("stept_refresh")
    assert original_cookie

    first = await client.post("/api/v1/auth/refresh")
    assert first.status_code == 200
    rotated_cookie = client.cookies.get("stept_refresh")
    assert rotated_cookie and rotated_cookie != original_cookie

    # Replaying the consumed token must fail and revoke the family.
    client.cookies.set("stept_refresh", original_cookie, path="/api/v1/auth")
    replay = await client.post("/api/v1/auth/refresh")
    assert replay.status_code == 401

    client.cookies.set("stept_refresh", rotated_cookie, path="/api/v1/auth")
    after_revoke = await client.post("/api/v1/auth/refresh")
    assert after_revoke.status_code == 401


async def test_unauthenticated_me_rejected(client):
    response = await client.get("/api/v1/me")
    assert response.status_code == 401


async def test_password_reset_flow(client, monkeypatch):
    await signup(client, "reset@example.com")

    captured: dict = {}

    async def fake_send(to, subject, html, **kwargs):
        captured["html"] = html
        return True

    monkeypatch.setattr("app.api.v1.auth.send_email", fake_send)
    response = await client.post("/api/v1/auth/password-reset", json={"email": "reset@example.com"})
    assert response.status_code == 200
    token = captured["html"].split("token=")[1].split('"')[0]

    confirm = await client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": token, "new_password": "brand-new-pass-1"},
    )
    assert confirm.status_code == 200

    login = await client.post(
        "/api/v1/auth/login", json={"email": "reset@example.com", "password": "brand-new-pass-1"}
    )
    assert login.status_code == 200


async def test_change_password_requires_current(client):
    auth = await signup(client, "cp@example.com")
    bad = await client.post(
        "/api/v1/me/change-password",
        json={"current_password": "wrong", "new_password": "whatever-123"},
        headers=bearer(auth),
    )
    assert bad.status_code == 401
    good = await client.post(
        "/api/v1/me/change-password",
        json={"current_password": "password-123", "new_password": "whatever-123"},
        headers=bearer(auth),
    )
    assert good.status_code == 200
