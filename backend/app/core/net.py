"""Egress guard for server-side fetches of operator-supplied URLs.

Anything a workspace can point the backend at — knowledge sources, outbound
webhooks, custom agent action endpoints — goes through :func:`assert_public_url`
first. Without it, "admin of a free workspace" is enough to read the host's
private network and its cloud metadata service through us.

Only http(s) URLs whose host is (or resolves exclusively to) a globally routable
address pass. Literal IPs and loopback names are decided locally, so they are
always enforced; the DNS leg is skipped for the ASGI ``testserver`` host and
under ``env=test``, where suites fetch respx-mocked hostnames that must never hit
real DNS.

Note the residual TOCTOU: a hostname that resolves publicly here can resolve
privately when httpx connects moments later (DNS rebinding). Closing that needs
connection-time pinning; this guard raises the bar to "attacker controls
authoritative DNS with a sub-timeout TTL".
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

from app.core.config import get_settings

# Resolve to loopback everywhere, so reject them without asking a resolver.
BLOCKED_HOSTNAMES = frozenset(
    {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}
)

ALLOWED_SCHEMES = ("http", "https")


class UnsafeUrlError(ValueError):
    """The URL is malformed, uses a rejected scheme, or targets a private host."""


def _literal_ip(hostname: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(hostname)
    except ValueError:
        return None


def _reject_if_not_global(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address, label: str
) -> None:
    # ::ffff:127.0.0.1 must be judged as the IPv4 address it embeds.
    mapped = getattr(address, "ipv4_mapped", None)
    if mapped is not None:
        address = mapped
    if not address.is_global:
        raise UnsafeUrlError(f"{label} resolves to a non-public address ({address})")


def assert_public_url(url: str, *, require_resolvable: bool = True) -> None:
    """Raise :class:`UnsafeUrlError` unless ``url`` targets a public http(s) host.

    ``require_resolvable=False`` keeps every private-address rejection but tolerates
    a host that does not resolve *right now*. Use it when validating saved config:
    DNS fails transiently, and a not-yet-provisioned or slow-to-propagate hostname
    is a support ticket, not an attack. The fetch itself always runs with the
    default, which is where the guarantee actually has to hold.
    """
    parts = urlsplit(url.strip())
    if parts.scheme not in ALLOWED_SCHEMES:
        raise UnsafeUrlError(f"Unsupported URL scheme: {parts.scheme or url!r}")
    hostname = parts.hostname
    if not hostname:
        raise UnsafeUrlError(f"Invalid URL: {url!r}")

    literal = _literal_ip(hostname)
    if literal is not None:
        _reject_if_not_global(literal, hostname)
        return
    if hostname.lower() in BLOCKED_HOSTNAMES:
        raise UnsafeUrlError(f"{hostname} resolves to a non-public address (loopback)")

    if hostname == "testserver" or get_settings().env == "test":
        return

    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
        infos = socket.getaddrinfo(hostname, port, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, ValueError) as exc:
        if not require_resolvable:
            return
        raise UnsafeUrlError(f"Could not resolve host: {hostname}") from exc
    if not infos:
        if not require_resolvable:
            return
        raise UnsafeUrlError(f"Could not resolve host: {hostname}")
    for info in infos:
        address = str(info[4][0]).split("%")[0]  # strip IPv6 zone id
        resolved = _literal_ip(address)
        if resolved is None:
            raise UnsafeUrlError(f"{hostname} resolves to an invalid address ({address})")
        _reject_if_not_global(resolved, hostname)


def is_public_url(url: str) -> bool:
    try:
        assert_public_url(url)
    except UnsafeUrlError:
        return False
    return True
