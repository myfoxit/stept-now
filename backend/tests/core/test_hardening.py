"""Production hardening: secret-key guard, security headers, docs gating, XFF parsing."""

import pytest

from app.core.config import DEFAULT_SECRET_KEY, Settings
from app.main import security_headers_for

# ---------------------------------------------------------------------------
# secret key guard
# ---------------------------------------------------------------------------


def _settings(**overrides) -> Settings:
    base = {
        "env": "prod",
        "secret_key": "x" * 48,
        "public_base_url": "https://app.example.com",
        "app_base_url": "https://app.example.com",
        # Settings reads .env by default; the test env must not leak in.
        "_env_file": None,
    }
    return Settings(**{**base, **overrides})


def test_prod_refuses_the_default_secret_key():
    with pytest.raises(RuntimeError, match="STEPT_SECRET_KEY is still the built-in default"):
        _settings(secret_key=DEFAULT_SECRET_KEY).assert_production_ready()


def test_prod_refuses_the_env_example_placeholder():
    with pytest.raises(RuntimeError, match="built-in default"):
        _settings(secret_key="change-me-in-prod").assert_production_ready()


def test_prod_refuses_a_short_secret_key():
    with pytest.raises(RuntimeError, match="at least 32"):
        _settings(secret_key="tooshort").assert_production_ready()


def test_prod_requires_https_base_urls():
    with pytest.raises(RuntimeError, match="PUBLIC_BASE_URL must be https"):
        _settings(public_base_url="http://app.example.com").assert_production_ready()
    with pytest.raises(RuntimeError, match="APP_BASE_URL must be https"):
        _settings(app_base_url="http://app.example.com").assert_production_ready()


def test_prod_accepts_a_proper_config():
    _settings().assert_production_ready()  # no raise


def test_dev_and_test_are_never_blocked():
    """The zero-dependency dev/test setup must keep working out of the box."""
    for env in ("dev", "test"):
        _settings(
            env=env,
            secret_key=DEFAULT_SECRET_KEY,
            public_base_url="http://localhost:8600",
            app_base_url="http://localhost:5273",
        ).assert_production_ready()


# ---------------------------------------------------------------------------
# docs gating
# ---------------------------------------------------------------------------


def test_docs_are_off_in_prod_and_on_elsewhere():
    assert _settings().docs_enabled is False
    assert _settings(env="dev").docs_enabled is True
    # …and can be turned back on deliberately.
    assert _settings(expose_api_docs=True).docs_enabled is True
    assert _settings(env="dev", expose_api_docs=False).docs_enabled is False


# ---------------------------------------------------------------------------
# security headers
# ---------------------------------------------------------------------------


def test_dashboard_paths_are_not_frameable():
    headers = security_headers_for("/api/v1/w/abc/conversations", https=True)
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "max-age=31536000" in headers["Strict-Transport-Security"]


@pytest.mark.parametrize("path", ["/widget-assets/loader.js", "/portal/acme"])
def test_embeddable_paths_stay_frameable(path):
    """The widget iframe and help-center portal are embedded on customer sites,
    so a framing ban there would break the product."""
    headers = security_headers_for(path, https=True)
    assert "X-Frame-Options" not in headers
    assert headers["X-Content-Type-Options"] == "nosniff"


def test_hsts_only_on_https():
    assert "Strict-Transport-Security" not in security_headers_for("/api/v1/me", https=False)


async def test_headers_reach_real_responses(client, workspace_ctx):
    response = await client.get(workspace_ctx.base, headers=workspace_ctx.owner_headers)
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "camera=()" in response.headers["permissions-policy"]


# ---------------------------------------------------------------------------
# X-Forwarded-For handling
# ---------------------------------------------------------------------------


def test_forwarded_for_is_ignored_without_a_trusted_proxy(monkeypatch):
    from app.core import ratelimit

    request = _fake_request({"x-forwarded-for": "1.2.3.4"}, client_host="10.0.0.9")
    monkeypatch.setattr(ratelimit, "get_settings", lambda: _settings(trusted_proxy_hops=0))
    assert ratelimit.client_identity(request) == "10.0.0.9"


def test_forwarded_for_is_read_from_the_right_when_trusted(monkeypatch):
    """Each proxy *appends* the address it saw, so with one trusted hop the real
    client is the last entry — and a client-supplied prefix cannot displace it."""
    from app.core import ratelimit

    monkeypatch.setattr(ratelimit, "get_settings", lambda: _settings(trusted_proxy_hops=1))

    # Caddy saw 203.0.113.7 and appended it.
    plain = _fake_request({"x-forwarded-for": "203.0.113.7"}, client_host="10.0.0.9")
    assert ratelimit.client_identity(plain) == "203.0.113.7"

    # The client tried to forge a header; our proxy appended the truth after it.
    spoofed = _fake_request({"x-forwarded-for": "9.9.9.9, 203.0.113.7"}, client_host="10.0.0.9")
    assert ratelimit.client_identity(spoofed) == "203.0.113.7"


def test_forwarded_for_with_two_trusted_hops(monkeypatch):
    from app.core import ratelimit

    monkeypatch.setattr(ratelimit, "get_settings", lambda: _settings(trusted_proxy_hops=2))
    request = _fake_request(
        {"x-forwarded-for": "spoofed, 203.0.113.7, 10.1.1.1"}, client_host="10.0.0.9"
    )
    assert ratelimit.client_identity(request) == "203.0.113.7"


def test_forwarded_for_falls_back_when_the_header_is_absent(monkeypatch):
    from app.core import ratelimit

    monkeypatch.setattr(ratelimit, "get_settings", lambda: _settings(trusted_proxy_hops=1))
    assert ratelimit.client_identity(_fake_request({}, client_host="10.0.0.9")) == "10.0.0.9"


def _fake_request(headers: dict[str, str], *, client_host: str):
    from starlette.datastructures import Headers

    class _Client:
        host = client_host

    class _Request:
        def __init__(self):
            self.headers = Headers(headers)
            self.client = _Client()

    return _Request()
