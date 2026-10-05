"""Exact identity signals, never fuzzy similarity."""

import re
import unicodedata
from urllib.parse import urlsplit, urlunsplit

from pydantic import HttpUrl


def normalize_name(value: str) -> str:
    text = unicodedata.normalize("NFKD", value.casefold())
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(re.sub(r"[^\w\s]", " ", text).split())


def normalize_url(value: str) -> str:
    parts = urlsplit(value if "://" in value else f"https://{value}")
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        raise ValueError("A public HTTP(S) URL is required")
    if parts.username or parts.password:
        raise ValueError("URLs must not contain credentials")
    host = parts.hostname.encode("idna").decode("ascii").lower().rstrip(".")
    if ":" in host:
        raise ValueError("IPv6 addresses are not company domains")
    port = parts.port
    netloc = host
    if port and (parts.scheme.lower(), port) not in {("https", 443), ("http", 80)}:
        netloc += f":{port}"
    return str(
        HttpUrl(urlunsplit((parts.scheme.lower(), netloc, parts.path or "/", parts.query, "")))
    )


def normalize_domain(value: str) -> str:
    host = urlsplit(normalize_url(value)).hostname
    assert host is not None
    if host.startswith("www."):
        host = host[4:]
    if not host or " " in host or "." not in host or len(host) > 253:
        raise ValueError("Invalid company domain")
    return host
