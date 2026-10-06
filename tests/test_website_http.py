from collections.abc import Sequence

import httpx
import pytest

from lead_engine.application.website import AuditSettings
from lead_engine.infrastructure.website.http_fetcher import HttpxFetcher
from lead_engine.infrastructure.website.security import (
    FetchPolicyError,
    PinnedPublicTransport,
    PublicUrlPolicy,
)

PUBLIC_IP = "93.184.216.34"


def resolver(host: str, port: int) -> Sequence[str]:
    return [PUBLIC_IP]


def settings(**changes: object) -> AuditSettings:
    return AuditSettings.model_validate({"min_request_interval_seconds": 0, **changes})


def test_real_adapter_pins_ip_retains_host_sni_and_supports_redirects() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.host == PUBLIC_IP
        assert request.headers["Host"] == "site.example"
        assert request.extensions["sni_hostname"] == "site.example"
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.path == "/":
            return httpx.Response(301, headers={"Location": "/home"})
        return httpx.Response(200, text="<h1>Home</h1>", headers={"Content-Type": "text/html"})

    fetcher = HttpxFetcher(
        settings(), policy=PublicUrlPolicy(resolver), transport=httpx.MockTransport(handler)
    )
    try:
        result = fetcher.fetch("https://site.example/")
        assert result.http_status == 200 and result.error_code is None
        assert result.final_url == "https://site.example/home"
        assert result.redirects == ("https://site.example/home",)
        assert result.body_size_bytes == len(b"<h1>Home</h1>")
        assert result.response_time_ms is not None
        assert len(requests) == 3
    finally:
        fetcher.close()


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/",
        "http://foo.localhost/",
        "http://127.0.0.1/",
        "http://127.99.3.1/",
        "http://[::1]/",
        "http://10.1.2.3/",
        "http://172.16.0.1/",
        "http://192.168.1.1/",
        "http://169.254.169.254/",
        "http://168.63.129.16/",
        "http://0.0.0.0/",
        "http://[fc00::1]/",
        "http://[fe80::1]/",
        "file:///etc/passwd",
        "ftp://example.com/",
        "http://metadata.google.internal/",
        "http://site.example:8080/",
        "https://user:password@site.example/",
        "http://[64:ff9b::a00:1]/",
    ],
)
def test_ssrf_blocked_before_transport(url: str) -> None:
    def no_network(request: httpx.Request) -> httpx.Response:
        raise AssertionError("Blocked target must never reach transport")

    fetcher = HttpxFetcher(
        settings(), policy=PublicUrlPolicy(resolver), transport=httpx.MockTransport(no_network)
    )
    try:
        assert fetcher.fetch(url).error_code in {"BLOCKED_ADDRESS", "BLOCKED_URL"}
    finally:
        fetcher.close()


def test_dns_private_and_mixed_answers_rejected() -> None:
    for answers in (["192.168.1.1"], [PUBLIC_IP, "10.0.0.1"]):

        def answer(host: str, port: int, current: Sequence[str] = answers) -> Sequence[str]:
            return current

        with pytest.raises(FetchPolicyError):
            PublicUrlPolicy(answer).resolve("https://site.example")


def test_redirect_private_destination_not_requested() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(302, headers={"Location": "http://169.254.169.254/latest/meta-data/"})

    fetcher = HttpxFetcher(
        settings(), policy=PublicUrlPolicy(resolver), transport=httpx.MockTransport(handler)
    )
    try:
        assert fetcher.fetch("https://site.example").error_code == "BLOCKED_ADDRESS"
        assert paths == ["/robots.txt", "/"]
    finally:
        fetcher.close()


def test_rebinding_is_checked_at_actual_transport_and_credentials_removed() -> None:
    calls = 0

    def changing(host: str, port: int) -> Sequence[str]:
        nonlocal calls
        calls += 1
        return [PUBLIC_IP] if calls == 1 else ["127.0.0.1"]

    policy = PublicUrlPolicy(changing)
    policy.resolve("https://site.example")
    transport = PinnedPublicTransport(
        policy, httpx.MockTransport(lambda request: httpx.Response(200))
    )
    with pytest.raises(FetchPolicyError):
        transport.handle_request(httpx.Request("GET", "https://site.example"))
    transport.close()


@pytest.mark.parametrize("status", [401, 403, 429, 404, 500])
def test_http_errors_not_retried(status: int) -> None:
    count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal count
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        count += 1
        return httpx.Response(status)

    fetcher = HttpxFetcher(
        settings(), policy=PublicUrlPolicy(resolver), transport=httpx.MockTransport(handler)
    )
    try:
        result = fetcher.fetch("https://site.example")
        assert result.http_status == status and result.error_code is not None and count == 1
    finally:
        fetcher.close()


@pytest.mark.parametrize(
    "error,expected", [(httpx.ReadTimeout, "TIMEOUT"), (httpx.ConnectError, "CONNECTION_ERROR")]
)
def test_timeout_connection_failure(error: type[httpx.RequestError], expected: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        raise error("private exception details", request=request)

    fetcher = HttpxFetcher(
        settings(), policy=PublicUrlPolicy(resolver), transport=httpx.MockTransport(handler)
    )
    try:
        result = fetcher.fetch("https://site.example")
        assert result.error_code == expected and "private" not in (result.error_message or "")
    finally:
        fetcher.close()


def test_connect_timeout_retries_once_and_succeeds() -> None:
    count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal count
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        count += 1
        if count == 1:
            raise httpx.ConnectTimeout("timeout", request=request)
        return httpx.Response(200, text="<h1>OK</h1>", headers={"Content-Type": "text/html"})

    fetcher = HttpxFetcher(
        settings(), policy=PublicUrlPolicy(resolver), transport=httpx.MockTransport(handler)
    )
    try:
        assert fetcher.fetch("https://site.example").error_code is None and count == 2
    finally:
        fetcher.close()


@pytest.mark.parametrize(
    "robots,expected",
    [
        ("User-agent: *\nDisallow: /", "ROBOTS_DENIED"),
        ("User-agent: *\nCrawl-delay: 20", "ROBOTS_DELAY"),
    ],
)
def test_robots_denial_and_delay_respected(robots: str, expected: str) -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(200, text=robots)

    fetcher = HttpxFetcher(
        settings(), policy=PublicUrlPolicy(resolver), transport=httpx.MockTransport(handler)
    )
    try:
        assert fetcher.fetch("https://site.example").error_code == expected
        assert paths == ["/robots.txt"]
    finally:
        fetcher.close()


@pytest.mark.parametrize(
    "headers,body,expected",
    [
        ({"Content-Length": "2000"}, b"x", "RESPONSE_TOO_LARGE"),
        ({}, b"x" * 2000, "RESPONSE_TOO_LARGE"),
        ({"Content-Encoding": "gzip"}, b"x", "UNSUPPORTED_ENCODING"),
    ],
)
def test_body_limits_and_compression_guard(
    headers: dict[str, str], body: bytes, expected: str
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, stream=httpx.ByteStream(body), headers=headers)

    fetcher = HttpxFetcher(
        settings(max_response_bytes=1024),
        policy=PublicUrlPolicy(resolver),
        transport=httpx.MockTransport(handler),
    )
    try:
        assert fetcher.fetch("https://site.example").error_code == expected
    finally:
        fetcher.close()


@pytest.mark.parametrize(
    "robots", ["<html>Access blocked</html>", "User-agent: *\nRequest-rate: 1/60"]
)
def test_robot_access_pages_and_rate_limit_are_not_bypassed(robots: str) -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(200, text=robots)

    fetcher = HttpxFetcher(
        settings(), policy=PublicUrlPolicy(resolver), transport=httpx.MockTransport(handler)
    )
    try:
        assert fetcher.fetch("https://site.example").error_code in {
            "ROBOTS_UNAVAILABLE",
            "ROBOTS_RATE_LIMIT",
        }
        assert paths == ["/robots.txt"]
    finally:
        fetcher.close()


def test_transport_does_not_forward_cookies_or_credentials() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert not {"cookie", "authorization", "proxy-authorization"} & set(request.headers)
        assert request.url.host == PUBLIC_IP
        return httpx.Response(200)

    transport = PinnedPublicTransport(PublicUrlPolicy(resolver), httpx.MockTransport(handler))
    try:
        transport.handle_request(
            httpx.Request(
                "GET",
                "https://site.example",
                headers={
                    "Cookie": "session=secret",
                    "Authorization": "secret",
                    "Proxy-Authorization": "secret",
                },
            )
        )
    finally:
        transport.close()
