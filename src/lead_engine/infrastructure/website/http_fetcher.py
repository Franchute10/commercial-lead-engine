"""Bounded anonymous GET retrieval with robots policy and manual guarded redirects."""

from time import monotonic, sleep
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from lead_engine.application.website import AuditSettings, FetchResult
from lead_engine.infrastructure.website.security import (
    FetchPolicyError,
    PinnedPublicTransport,
    PublicUrlPolicy,
)


class HttpxFetcher:
    def __init__(
        self,
        settings: AuditSettings | None = None,
        *,
        policy: PublicUrlPolicy | None = None,
        transport: httpx.BaseTransport | None = None,
        same_domain_only: bool = False,
    ) -> None:
        self.same_domain_only = same_domain_only
        self.settings = settings or AuditSettings()
        self.policy = policy or PublicUrlPolicy()
        self._transport = PinnedPublicTransport(self.policy, transport)
        self._last_request = 0.0
        self._client = httpx.Client(
            transport=self._transport,
            trust_env=False,
            follow_redirects=False,
            headers={
                "User-Agent": self.settings.user_agent,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Encoding": "identity",
            },
        )

    def close(self) -> None:
        self._client.close()

    def _pace(self, deadline: float) -> None:
        wait = self.settings.min_request_interval_seconds - (monotonic() - self._last_request)
        if wait > 0:
            if monotonic() + wait >= deadline:
                raise FetchPolicyError("TIMEOUT", "Total fetch budget exhausted")
            sleep(wait)
        self._last_request = monotonic()

    def _request(
        self, client: httpx.Client, url: str, deadline: float, *, robots: bool = False
    ) -> tuple[int, dict[str, str], str, int, float]:
        if self.same_domain_only and urlsplit(url).hostname != self._research_host:
            raise FetchPolicyError(
                "EXTERNAL_DOMAIN", "Contact research stays on the company domain"
            )
        self.policy.resolve(url)  # Validate before any request; transport revalidates and pins.
        for attempt in range(self.settings.retry_count + 1):
            self._pace(deadline)
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise FetchPolicyError("TIMEOUT", "Total fetch budget exhausted")
            started = monotonic()
            try:
                with client.stream(
                    "GET", url, timeout=min(self.settings.timeout_seconds, remaining)
                ) as response:
                    headers = dict(response.headers)
                    code = response.status_code
                    # No body analysis/download for errors or redirects.
                    if not 200 <= code < 300:
                        return code, headers, "", 0, (monotonic() - started) * 1000
                    if headers.get("content-encoding", "identity").lower() not in {"", "identity"}:
                        raise FetchPolicyError(
                            "UNSUPPORTED_ENCODING", "Compressed responses are not inspected"
                        )
                    cap = (
                        min(self.settings.max_response_bytes, 100000)
                        if robots
                        else self.settings.max_response_bytes
                    )
                    length = headers.get("content-length", "")
                    if length.isdigit() and int(length) > cap:
                        raise FetchPolicyError(
                            "RESPONSE_TOO_LARGE", "Declared response size exceeds limit"
                        )
                    chunks: list[bytes] = []
                    size = 0
                    # Identity encoding prevents compression expansion.
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > cap:
                            raise FetchPolicyError(
                                "RESPONSE_TOO_LARGE", "Decoded response exceeds limit"
                            )
                        if monotonic() >= deadline:
                            raise FetchPolicyError("TIMEOUT", "Total fetch budget exhausted")
                        chunks.append(chunk)
                    body = b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")
                    return code, headers, body, size, (monotonic() - started) * 1000
            except httpx.ConnectTimeout:
                # Only GET connect-timeout is retried once; no HTTP-error/TLS/read retries.
                if attempt == self.settings.retry_count:
                    raise
        raise RuntimeError("Unreachable retry state")

    def _robots(self, client: httpx.Client, page: str, deadline: float) -> RobotFileParser:
        current = urljoin(page, "/robots.txt")
        parser = RobotFileParser(current)
        for _ in range(self.settings.max_redirects + 1):
            code, headers, body, _, _ = self._request(client, current, deadline, robots=True)
            if code in {301, 302, 303, 307, 308} and headers.get("location"):
                current = urljoin(current, headers["location"])
                self.policy.resolve(current)
                continue
            if code in {404, 410}:
                parser.parse([])
                return parser
            if not 200 <= code < 300:
                raise FetchPolicyError(
                    "ROBOTS_UNAVAILABLE", "Robots policy could not be safely obtained"
                )
            if body.lstrip().startswith("<"):
                raise FetchPolicyError(
                    "ROBOTS_UNAVAILABLE", "Robots response appears to be an HTML access page"
                )
            parser.parse(body.splitlines())
            return parser
        raise FetchPolicyError("ROBOTS_REDIRECT_LIMIT", "Robots redirect limit exceeded")

    def fetch(self, url: str) -> FetchResult:
        self._research_host = urlsplit(url).hostname
        current = url
        hops: list[str] = []
        status: int | None = None
        elapsed: float | None = None
        size = 0
        deadline = monotonic() + self.settings.total_fetch_seconds
        try:
            self.policy.resolve(url)
            client = self._client
            policies: dict[str, RobotFileParser] = {}
            for redirect_count in range(self.settings.max_redirects + 1):
                parsed, _ = self.policy.resolve(current)
                current = str(parsed)
                origin = str(parsed.copy_with(path="/", query=None, fragment=None))
                if origin not in policies:
                    policies[origin] = self._robots(client, current, deadline)
                rules = policies[origin]
                if not rules.can_fetch("CommercialLeadEngine", current):
                    raise FetchPolicyError(
                        "ROBOTS_DENIED", "Robots policy disallows the requested page"
                    )
                requested_interval = float(
                    rules.crawl_delay("CommercialLeadEngine") or rules.crawl_delay("*") or 0
                )
                if requested_interval > self.settings.min_request_interval_seconds:
                    # Do not ignore a longer requested crawl delay or perform an unbounded wait.
                    raise FetchPolicyError(
                        "ROBOTS_DELAY", "Robots requests a longer crawl delay; audit deferred"
                    )
                rate = rules.request_rate("CommercialLeadEngine") or rules.request_rate("*")
                if (
                    rate
                    and rate.seconds / rate.requests > self.settings.min_request_interval_seconds
                ):
                    raise FetchPolicyError(
                        "ROBOTS_RATE_LIMIT", "Robots requests a slower request rate; audit deferred"
                    )
                status, headers, body, size, elapsed = self._request(client, current, deadline)
                if status in {301, 302, 303, 307, 308}:
                    if redirect_count >= self.settings.max_redirects:
                        raise FetchPolicyError("REDIRECT_LIMIT", "Homepage redirect limit exceeded")
                    location = headers.get("location")
                    if not location:
                        raise FetchPolicyError(
                            "INVALID_REDIRECT", "Redirect has no Location header"
                        )
                    destination = urljoin(current, location)
                    validated, _ = self.policy.resolve(destination)
                    destination = str(validated)
                    hops.append(destination)
                    current = destination
                    continue
                if not 200 <= status < 300:
                    code = "ACCESS_BLOCKED" if status in {401, 403, 429} else "HTTP_ERROR"
                    return FetchResult(
                        requested_url=url,
                        final_url=current,
                        http_status=status,
                        response_time_ms=elapsed,
                        body_size_bytes=size,
                        redirects=tuple(hops),
                        error_code=code,
                        error_message="HTTP status prevented public page inspection",
                    )
                return FetchResult(
                    requested_url=url,
                    final_url=current,
                    http_status=status,
                    response_time_ms=elapsed,
                    body=body,
                    body_size_bytes=size,
                    content_type=headers.get("content-type", ""),
                    redirects=tuple(hops),
                )
            raise FetchPolicyError("REDIRECT_LIMIT", "Homepage redirect limit exceeded")
        except FetchPolicyError as error:
            code, message = error.code, str(error)
        except httpx.TimeoutException:
            code, message = "TIMEOUT", "HTTP request timed out"
        except httpx.ConnectError:
            code, message = "CONNECTION_ERROR", "HTTP connection/TLS validation failed"
        except httpx.HTTPError:
            code, message = "HTTP_CLIENT_ERROR", "HTTP client could not safely read the response"
        return FetchResult(
            requested_url=url,
            final_url=current,
            http_status=status,
            response_time_ms=elapsed,
            body_size_bytes=size,
            redirects=tuple(hops),
            error_code=code,
            error_message=message,
        )
