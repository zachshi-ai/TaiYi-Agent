"""SSRF protection for URL-capable tools.

Rejects requests to private/loopback/link-local address space, enforces an
optional host allowlist, and resolves hostnames so a public name pointing at an
internal IP (DNS rebinding) is still caught. Fail-closed: anything we cannot
positively clear is refused.
"""
from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from urllib.parse import urlparse

_PRIVATE_NETS = [
    ipaddress.ip_network(n)
    for n in (
        "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
        "169.254.0.0/16", "0.0.0.0/8",
        "::1/128", "fc00::/7", "fe80::/10",
    )
]

Resolver = Callable[[str], list[str]]


class SSRFError(Exception):
    """Raised when a URL is refused. Fail-closed — refusal is the safe default."""


def _default_resolver(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, None)
    return [info[4][0] for info in infos]


def _is_private(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # unparseable -> treat as unsafe
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_unspecified:
        return True
    return any(ip in net for net in _PRIVATE_NETS)


class SSRFGuard:
    def __init__(
        self,
        allowlist: tuple[str, ...] = (),
        *,
        require_allowlist: bool = False,
        resolver: Resolver | None = None,
    ):
        self.allowlist = set(allowlist)
        self.require_allowlist = require_allowlist
        self._resolve = resolver or _default_resolver

    def check(self, url: str) -> None:
        """Raise SSRFError if ``url`` must not be fetched; return None if cleared."""
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise SSRFError(f"scheme not allowed: {parsed.scheme or '(none)'}")
        host = parsed.hostname
        if not host:
            raise SSRFError("no host in URL")

        if self.allowlist:
            if host not in self.allowlist:
                raise SSRFError(f"host not in allowlist: {host}")
        elif self.require_allowlist:
            raise SSRFError("no allowlist configured (fail-closed)")

        # If the host is a literal IP, check it directly; otherwise resolve.
        try:
            ipaddress.ip_address(host)
            candidates = [host]
        except ValueError:
            try:
                candidates = self._resolve(host)
            except Exception as e:  # noqa: BLE001 — resolution failure is fail-closed
                raise SSRFError(f"could not resolve host {host}: {e}") from e
            if not candidates:
                raise SSRFError(f"host {host} resolved to nothing")

        for ip in candidates:
            if _is_private(ip):
                raise SSRFError(f"resolves to private/internal address: {ip}")

    def is_allowed(self, url: str) -> bool:
        try:
            self.check(url)
            return True
        except SSRFError:
            return False
