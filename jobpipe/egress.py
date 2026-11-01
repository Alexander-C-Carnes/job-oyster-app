"""Every request the app sends out is made here (docs/paid-service.md item 1.2).

In hosted mode (hosted.enabled()) a request may reach only a public address, over http or https: never loopback,
a private network, link-local (cloud metadata lives at 169.254.169.254), or anything else that isn't globally
routable. The check runs after the DNS lookup, on every request and every redirect hop, and the connection goes to
the very address that was checked, so a name that answers with a public address for the check and a private one for
the connection (DNS rebinding) gets nowhere. A name with any non-public address is refused outright.

On the website the user's copy has no network of its own (hosted/systemd/jobpipe@.service): OYSTER_EGRESS_PROXY names
the egress proxy every checked request goes through.

With hosted mode off, client() and async_client() are plain httpx clients: postings, job sources and an AI running on
this Mac (Ollama, LM Studio) are reached exactly as before. tests/test_egress.py fails when code makes a request any
other way.
"""
from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
from typing import Callable, Optional

import httpx

from . import hosted

PROXY = "OYSTER_EGRESS_PROXY"
Resolve = Callable[[str, int], list[str]]          # (host, port) -> its addresses
NAT64 = ipaddress.ip_network("64:ff9b::/96")       # IPv6 addresses that carry an IPv4 one in their last 32 bits


class Blocked(httpx.TransportError):
    """A request the guard turned away. An httpx.TransportError, so code that copes with a host it can't reach copes
    with this too."""


def enforced() -> bool:
    return hosted.enabled()


def proxy() -> Optional[str]:
    """The egress proxy's address in hosted mode, else None."""
    return (os.environ.get(PROXY, "").strip() or None) if enforced() else None


def system_resolve(host: str, port: int) -> list[str]:
    return list(dict.fromkeys(a[4][0] for a in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)))


def is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%")[0])
    if ip.version == 6:
        if ip.teredo:
            return False
        inner = ip.ipv4_mapped or ip.sixtofour or (ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF) if ip in NAT64 else None)
        if inner is not None:
            return is_public(str(inner))
    return ip.is_global and not (ip.is_multicast or ip.is_reserved or ip.is_unspecified)


def check_url(url: str) -> None:
    if httpx.URL(url).scheme not in ("http", "https"):
        raise Blocked(f"Only http and https addresses can be reached, not {url[:80]}")


def public_address(host: str, port: int, resolve: Resolve = system_resolve) -> str:
    """The address to connect to for host, once every address it resolves to has been checked."""
    try:
        addresses = [str(ipaddress.ip_address(host))]
    except ValueError:
        try:
            addresses = resolve(host, port)
        except OSError as e:
            raise httpx.ConnectError(f"Couldn't look up {host} ({e})") from None
    if not addresses:
        raise httpx.ConnectError(f"Couldn't look up {host}")
    for a in addresses:
        if not is_public(a):
            raise Blocked(f"{host} leads to {a}, which isn't a public address, so it can't be reached.")
    return addresses[0]


def _port(url: httpx.URL) -> int:
    return url.port or (443 if url.scheme == "https" else 80)


def _pinned(request: httpx.Request, address: str) -> httpx.Request:
    """request, sent to `address`: the Host header and the TLS name (SNI and the certificate check) stay the host's.
    A copy, so the client still sees the host's URL (for relative redirects and cookies)."""
    host = request.url.host
    if address == host:
        return request
    ext = {**request.extensions, "sni_hostname": host} if request.url.scheme == "https" else request.extensions
    return httpx.Request(request.method, request.url.copy_with(host=address), headers=request.headers,
                         stream=request.stream, extensions=ext)


class GuardTransport(httpx.BaseTransport):
    def __init__(self, inner: Optional[httpx.BaseTransport] = None, resolve: Resolve = system_resolve):
        self.inner = inner or httpx.HTTPTransport(proxy=proxy())
        self.resolve = resolve

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        check_url(str(request.url))
        address = public_address(request.url.host, _port(request.url), self.resolve)
        return self.inner.handle_request(_pinned(request, address))

    def close(self) -> None:
        self.inner.close()


class AsyncGuardTransport(httpx.AsyncBaseTransport):
    def __init__(self, inner: Optional[httpx.AsyncBaseTransport] = None, resolve: Resolve = system_resolve):
        self.inner = inner or httpx.AsyncHTTPTransport(proxy=proxy())
        self.resolve = resolve

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        check_url(str(request.url))
        address = await asyncio.to_thread(public_address, request.url.host, _port(request.url), self.resolve)
        return await self.inner.handle_async_request(_pinned(request, address))

    async def aclose(self) -> None:
        await self.inner.aclose()


def client(*, transport: Optional[httpx.BaseTransport] = None, resolve: Resolve = system_resolve, **kw) -> httpx.Client:
    """An httpx.Client (same arguments), guarded in hosted mode."""
    if not enforced():
        return httpx.Client(transport=transport, **kw)
    return httpx.Client(transport=GuardTransport(transport, resolve), **kw)


def async_client(*, transport: Optional[httpx.AsyncBaseTransport] = None, resolve: Resolve = system_resolve,
                 **kw) -> httpx.AsyncClient:
    """An httpx.AsyncClient (same arguments), guarded in hosted mode."""
    if not enforced():
        return httpx.AsyncClient(transport=transport, **kw)
    return httpx.AsyncClient(transport=AsyncGuardTransport(transport, resolve), **kw)
