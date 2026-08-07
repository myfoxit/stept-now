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


async def test_concurrent_refresh_within_grace_is_benign(client):
    """Two tabs sharing one cookie both refresh: the second must NOT nuke the
    session family (this was the 'I keep getting logged out' bug)."""
    await signup(client, "rot@example.com")
    original_cookie = client.cookies.get("stept_refresh")
    assert original_cookie

    first = await client.post("/api/v1/auth/refresh")
    assert first.status_code == 200
    rotated_cookie = client.cookies.get("stept_refresh")
    assert rotated_cookie and rotated_cookie != original_cookie

    # Tab 2 replays the just-consumed token → sibling session, not a family wipe.
    client.cookies.delete("stept_refresh")
    client.cookies.set("stept_refresh", original_cookie, path="/api/v1/auth")
    replay = await client.post("/api/v1/auth/refresh")
    assert replay.status_code == 200
    sibling_cookie = replay.cookies.get("stept_refresh")
    assert sibling_cookie and sibling_cookie != original_cookie

    # Both live chains keep working.
    client.cookies.delete("stept_refresh")
    client.cookies.set("stept_refresh", rotated_cookie, path="/api/v1/auth")
    assert (await client.post("/api/v1/auth/refresh")).status_code == 200
    client.cookies.delete("stept_refresh")
    client.cookies.set("stept_refresh", sibling_cookie, path="/api/v1/auth")
    assert (await client.post("/api/v1/auth/refresh")).status_code == 200


async def test_stale_reuse_still_revokes_family(client, session):
    """Replay outside the grace window is theft: every live token dies."""
    from datetime import timedelta

    from sqlalchemy import update as sa_update

    from app.core.db import utcnow
    from app.models.user import RefreshToken

    await signup(client, "theft@example.com")
    original_cookie = client.cookies.get("stept_refresh")
    assert original_cookie

    first = await client.post("/api/v1/auth/refresh")
    assert first.status_code == 200
    rotated_cookie = client.cookies.get("stept_refresh")

    # Age the rotation beyond the grace window.
    await session.execute(
        sa_update(RefreshToken)
        .where(RefreshToken.revoked_at.is_not(None))
        .values(revoked_at=utcnow() - timedelta(minutes=10))
    )
    await session.commit()

    client.cookies.set("stept_refresh", original_cookie, path="/api/v1/auth")
    replay = await client.post("/api/v1/auth/refresh")
    assert replay.status_code == 401

    # The whole family is gone, including the legitimate successor.
    client.cookies.set("stept_refresh", rotated_cookie, path="/api/v1/auth")
    after_revoke = await client.post("/api/v1/auth/refresh")
    assert after_revoke.status_code == 401


async def test_logged_out_token_gets_no_grace(client):
    """Grace applies only to rotation races — a cookie revoked by logout stays dead."""
    await signup(client, "lo@example.com")
    cookie = client.cookies.get("stept_refresh")
    assert cookie

    assert (await client.post("/api/v1/auth/logout")).status_code == 200

    client.cookies.set("stept_refresh", cookie, path="/api/v1/auth")
    replay = await client.post("/api/v1/auth/refresh")
    assert replay.status_code == 401


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
