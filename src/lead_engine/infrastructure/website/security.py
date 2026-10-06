"""Resolve all addresses, reject non-public destinations, and pin the actual connection."""

import ipaddress
import socket
from collections.abc import Callable, Sequence

import httpx


class FetchPolicyError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


Resolver = Callable[[str, int], Sequence[str]]


def system_resolver(host: str, port: int) -> Sequence[str]:
    return tuple(
        {str(item[4][0]) for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}
    )


class PublicUrlPolicy:
    def __init__(self, resolver: Resolver = system_resolver) -> None:
        self._resolver = resolver

    def resolve(self, value: str) -> tuple[httpx.URL, str]:
        try:
            url = httpx.URL(value)
        except httpx.InvalidURL as error:
            raise FetchPolicyError("BLOCKED_URL", "Invalid HTTP URL") from error
        host = url.host.lower().rstrip(".")
        port = url.port or (443 if url.scheme == "https" else 80)
        if (
            url.scheme not in {"http", "https"}
            or not host
            or url.username
            or url.password
            or len(str(url)) > 1000
            or port not in {80, 443}
        ):
            raise FetchPolicyError(
                "BLOCKED_URL", "Only unauthenticated HTTP(S) URLs on ports 80/443 are allowed"
            )
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
            raise FetchPolicyError("BLOCKED_ADDRESS", "Local/internal hostnames are blocked")
        try:
            literal = ipaddress.ip_address(host)
            addresses: Sequence[str] = (str(literal),)
        except ValueError:
            try:
                addresses = self._resolver(host, port)
            except OSError as error:
                raise FetchPolicyError("DNS_ERROR", "Public hostname resolution failed") from error
        if not addresses:
            raise FetchPolicyError("DNS_ERROR", "Hostname resolved to no addresses")
        for address in addresses:
            try:
                ip = ipaddress.ip_address(address)
            except ValueError as error:
                raise FetchPolicyError("BLOCKED_ADDRESS", "Invalid resolved address") from error
            if (
                not ip.is_global
                or ip.is_multicast
                or ip.is_reserved
                or str(ip) in {"168.63.129.16", "100.100.100.200"}
            ):
                raise FetchPolicyError("BLOCKED_ADDRESS", "Non-public destination blocked")
            if isinstance(ip, ipaddress.IPv6Address):
                if (
                    ip.ipv4_mapped
                    or ip.sixtofour
                    or ip.teredo
                    or ip in ipaddress.ip_network("64:ff9b::/96")
                    or ip in ipaddress.ip_network("64:ff9b:1::/48")
                ):
                    raise FetchPolicyError(
                        "BLOCKED_ADDRESS", "IPv6 transition destinations blocked"
                    )
        return url.copy_with(fragment=None), str(addresses[0])


class PinnedPublicTransport(httpx.BaseTransport):
    """Pin resolved IP while retaining Host and TLS SNI/certificate verification.

    No reusable connections: different hostnames sharing an IP must not reuse a TLS session.
    The injected inner transport is for deterministic tests, never a CLI security override.
    """

    def __init__(self, policy: PublicUrlPolicy, inner: httpx.BaseTransport | None = None) -> None:
        self.policy = policy
        self.inner = inner or httpx.HTTPTransport(
            retries=0,
            trust_env=False,
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=0),
        )

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        url, address = self.policy.resolve(str(request.url))
        headers = httpx.Headers(request.headers)
        headers["Host"] = url.netloc.decode("ascii")
        for name in ("Authorization", "Proxy-Authorization", "Cookie"):
            headers.pop(name, None)
        pinned = httpx.Request(
            request.method,
            url.copy_with(host=address),
            headers=headers,
            content=request.content,
            extensions={**request.extensions, "sni_hostname": url.host},
        )
        return self.inner.handle_request(pinned)

    def close(self) -> None:
        self.inner.close()
