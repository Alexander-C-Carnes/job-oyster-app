"""Find JobsPipe's address with DNS over HTTPS instead of the network's own DNS.

Some networks filter DNS: an ISP's "safe browsing" filter can answer api.jobspipe.dev with a
block page whose certificate fails, so every search failed with CERTIFICATE_VERIFY_FAILED. Only the
lookup changes: the connection still goes to api.jobspipe.dev's name for TLS, so its certificate is
checked as before, and every other host the app reaches keeps the network's DNS.
"""
from __future__ import annotations

import ipaddress
import time
from typing import Callable, Optional

import httpx

from . import egress

# Asked by IP address, so no DNS lookup is needed to reach them.
RESOLVERS = ("https://1.1.1.1/dns-query", "https://8.8.8.8/resolve")
MIN_TTL = 300  # seconds an answer is reused, however short the record's own TTL


def lookup(host: str, client: Optional[httpx.Client] = None) -> tuple[str, int]:
    """(IPv4 address, TTL) for host from the first resolver that answers, or ("", 0)."""
    c = client or egress.client(timeout=5)
    for url in RESOLVERS:
        try:
            r = c.get(url, params={"name": host, "type": "A"}, headers={"accept": "application/dns-json"})
            answers = (r.json().get("Answer") or []) if r.status_code == 200 else []
        except (httpx.HTTPError, ValueError):
            continue
        for a in answers:
            if a.get("type") == 1:
                try:
                    return str(ipaddress.IPv4Address(a.get("data"))), int(a.get("TTL") or 0)
                except ValueError:
                    continue
    return "", 0


class DoHTransport(httpx.BaseTransport):
    """Sends requests for `hosts` to the address DNS over HTTPS gives, keeping the host name for TLS
    (SNI and the certificate check) and the Host header. Falls back to normal DNS when no resolver answers."""

    def __init__(self, hosts: set[str], inner: Optional[httpx.BaseTransport] = None,
                 resolve: Callable[[str], tuple[str, int]] = lookup):
        self.hosts = {h.lower() for h in hosts}
        self.inner = inner or httpx.HTTPTransport()
        self.resolve = resolve
        self._cache: dict[str, tuple[str, float]] = {}

    def _address(self, host: str) -> str:
        ip, until = self._cache.get(host, ("", 0.0))
        if ip and until > time.monotonic():
            return ip
        ip, ttl = self.resolve(host)
        if ip:
            self._cache[host] = (ip, time.monotonic() + max(ttl, MIN_TTL))
        return ip

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host.lower()
        if host in self.hosts and (ip := self._address(host)):
            request.url = request.url.copy_with(host=ip)
            request.extensions = {**request.extensions, "sni_hostname": host}
        return self.inner.handle_request(request)

    def close(self) -> None:
        self.inner.close()
