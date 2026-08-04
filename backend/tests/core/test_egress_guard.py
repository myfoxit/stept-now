"""The shared SSRF/egress guard and the three surfaces that must enforce it.

Literal addresses and loopback names are decided without DNS, so these assert
real blocking even under env=test (where the resolver leg is deliberately
skipped so respx-mocked hostnames never hit real DNS).
"""

import pytest

from app.core.net import UnsafeUrlError, assert_public_url, is_public_url

PRIVATE_URLS = [
    "http://127.0.0.1/",
    "http://127.0.0.1:8600/api/v1/me",
    "https://10.0.0.5/internal",
    "http://192.168.1.1/",
    "http://172.16.0.1/",
    "http://169.254.169.254/latest/meta-data/",  # cloud metadata
    "http://[::1]/",
    "http://[::ffff:127.0.0.1]/",  # IPv4-mapped loopback
    "http://localhost/",
    "http://localhost:22/",
    "http://LOCALHOST/",
    "http://ip6-localhost/",
    "http://0.0.0.0/",
]

BAD_SCHEMES = ["file:///etc/passwd", "gopher://x/", "ftp://x/", "redis://localhost:6379", "//evil"]


@pytest.mark.parametrize("url", PRIVATE_URLS)
def test_private_targets_are_rejected(url):
    with pytest.raises(UnsafeUrlError):
        assert_public_url(url)
    assert is_public_url(url) is False


@pytest.mark.parametrize("url", BAD_SCHEMES)
def test_non_http_schemes_are_rejected(url):
    with pytest.raises(UnsafeUrlError):
        assert_public_url(url)


@pytest.mark.parametrize(
    "url", ["https://8.8.8.8/", "https://example.com/hook", "http://testserver/x"]
)
def test_public_targets_pass(url):
    assert_public_url(url)
    assert is_public_url(url) is True


# ---------------------------------------------------------------------------
# surface 1: outbound webhooks
# ---------------------------------------------------------------------------


async def test_webhook_rejects_private_url(client, workspace_ctx):
    for url in (
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1:8600/x",
        "http://localhost/x",
    ):
        response = await client.post(
            f"{workspace_ctx.base}/webhooks",
            json={"url": url, "events": ["conversation.created"]},
            headers=workspace_ctx.owner_headers,
        )
        assert response.status_code == 422, f"{url} → {response.status_code}"
        assert "public host" in response.text

    ok = await client.post(
        f"{workspace_ctx.base}/webhooks",
        json={"url": "https://hooks.example.com/stept", "events": ["conversation.created"]},
        headers=workspace_ctx.owner_headers,
    )
    assert ok.status_code == 201, ok.text


async def test_webhook_delivery_blocks_private_url_at_send_time():
    """A row that predates the schema guard still must not be delivered."""
    from app.services.webhooks import _post

    success, code, error = await _post("http://169.254.169.254/latest/meta-data/", b"{}", {})
    assert success is False
    assert code is None
    assert "blocked" in (error or "")


# ---------------------------------------------------------------------------
# surface 2: custom agent actions (their response body reaches the agent)
# ---------------------------------------------------------------------------


async def test_custom_action_rejects_private_url(client, workspace_ctx):
    response = await client.post(
        f"{workspace_ctx.base}/ai/actions",
        json={
            "name": "imds",
            "method": "GET",
            "url": "http://169.254.169.254/latest/meta-data/",
            "params_schema": {},
        },
        headers=workspace_ctx.owner_headers,
    )
    assert response.status_code == 422, response.text
    assert "public host" in response.text


async def test_custom_action_execution_blocks_private_url():
    """Defence in depth: execution refuses even if the row was written directly."""
    from app.agents.tools import execute_custom_action
    from app.models.agent import CustomAction

    action = CustomAction(
        workspace_id="w",
        name="imds",
        description="",
        method="GET",
        url="http://169.254.169.254/latest/meta-data/",
        headers={},
        params_schema={},
        timeout_s=5,
    )
    result = await execute_custom_action(action, {})
    assert result.ok is False
    assert "blocked" in (result.error or "")
    assert result.body is None


# ---------------------------------------------------------------------------
# surface 3: knowledge fetches keep their existing FetchError contract
# ---------------------------------------------------------------------------


def test_connector_guard_still_raises_fetch_error():
    from app.rag.connectors import check_public_url
    from app.rag.tasks import FetchError

    with pytest.raises(FetchError):
        check_public_url("http://169.254.169.254/")
    check_public_url("https://example.com/sitemap.xml")


# ---------------------------------------------------------------------------
# resolvability: a config-time convenience, never a security relaxation
# ---------------------------------------------------------------------------


def test_unresolvable_host_is_tolerated_at_save_time_only(monkeypatch):
    """DNS fails transiently and hostnames get provisioned late, so saving config
    must not hard-fail on "cannot resolve" — while the fetch itself still does."""
    import socket as socket_module

    from app.core import net

    # env=test short-circuits before DNS, so exercise the resolver leg directly.
    monkeypatch.setattr(net, "get_settings", lambda: _prod_settings())

    def _no_such_host(*args, **kwargs):
        raise socket_module.gaierror("Name or service not known")

    monkeypatch.setattr(net.socket, "getaddrinfo", _no_such_host)

    net.assert_public_url("https://not-provisioned-yet.example.com/hook", require_resolvable=False)
    with pytest.raises(UnsafeUrlError, match="Could not resolve host"):
        net.assert_public_url("https://not-provisioned-yet.example.com/hook")


def test_private_resolution_is_rejected_even_when_resolvability_is_optional(monkeypatch):
    """The lenient flag must not become an SSRF bypass via a DNS record."""
    from app.core import net

    monkeypatch.setattr(net, "get_settings", lambda: _prod_settings())
    monkeypatch.setattr(
        net.socket,
        "getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("127.0.0.1", 443))],
    )
    for lenient in (True, False):
        with pytest.raises(UnsafeUrlError, match="non-public address"):
            net.assert_public_url("https://rebind.example.com/", require_resolvable=not lenient)


def _prod_settings():
    from app.core.config import Settings

    return Settings(env="prod", secret_key="x" * 48, _env_file=None)
