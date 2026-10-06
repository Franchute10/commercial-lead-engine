"""Explicit manual and bounded official website contact providers."""

import json
import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
from pydantic import ValidationError

from lead_engine.application.website import HttpFetcher
from lead_engine.domain.contact_research import ContactCandidate
from lead_engine.domain.enums import RoleCategory, SourceType
from lead_engine.domain.identity import normalize_name
from lead_engine.domain.models import Company, utc_now
from lead_engine.infrastructure.website.html_analyzer import BeautifulSoupHtmlAnalyzer

ROLE_PATTERNS: tuple[tuple[str, RoleCategory], ...] = (
    (r"(?:propietari[oa]|dueñ[oa]|owner)", RoleCategory.OWNER),
    (r"(?:fundador[ae]?|founder)", RoleCategory.FOUNDER),
    (
        r"(?:gerente general|general manager|restaurant manager|gerente de restaurante)",
        RoleCategory.GENERAL_MANAGEMENT,
    ),
    (r"(?:director[a]? m[eé]dic[oa]|medical director)", RoleCategory.MEDICAL_DIRECTOR),
    (
        r"(?:gerente comercial|commercial manager|sales manager|business development manager)",
        RoleCategory.COMMERCIAL,
    ),
    (
        r"(?:marketing manager|marketing analyst|brand manager|gerente de marketing|"
        r"director[a]? de marketing)",
        RoleCategory.MARKETING,
    ),
    (r"(?:digital manager|ecommerce manager|gerente digital)", RoleCategory.DIGITAL),
    (r"(?:customer experience manager)", RoleCategory.CUSTOMER_EXPERIENCE),
    (
        r"(?:operations manager|gerente de operaciones|administrador[a]?)",
        RoleCategory.ADMINISTRATION,
    ),
)
LINK_PATTERN = re.compile(
    r"(?:about|nosotros|equipo|team|staff|contact|contacto|directorio|management|leadership)", re.I
)
NAME_PATTERN = (
    r"(?:Dr\.?\s+|Dra\.?\s+)?[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:[ \-][A-ZÁÉÍÓÚÑ][a-záéíóúñ]+){1,4}"
)


def category_for(title: str) -> RoleCategory:
    for pattern, category in ROLE_PATTERNS:
        if re.fullmatch(pattern, title.strip(), re.I):
            return category
    return RoleCategory.UNKNOWN


class ManualContactProvider:
    name = "manual"

    def __init__(self, candidates: list[ContactCandidate]) -> None:
        self.candidates = candidates

    @property
    def warnings(self) -> list[str]:
        return []

    def discover(self, company: Company) -> list[ContactCandidate]:
        return list(self.candidates)


class StaticPublicSearchProvider:
    def __init__(self, fixtures: dict[str, list[ContactCandidate]]) -> None:
        self.fixtures = fixtures

    def search(self, query: str, limit: int = 5) -> list[ContactCandidate]:
        return self.fixtures.get(query, [])[:limit]


class CompanyWebsiteContactProvider:
    name = "company-website-v1"

    def __init__(self, fetcher: HttpFetcher, max_pages: int = 3) -> None:
        if not 1 <= max_pages <= 5:
            raise ValueError("Page budget must be 1–5")
        self.fetcher = fetcher
        self.max_pages = max_pages
        self._warnings: list[str] = []

    @property
    def warnings(self) -> list[str]:
        return list(self._warnings)

    def discover(self, company: Company) -> list[ContactCandidate]:
        self._warnings = []
        if not company.website:
            return []
        host = urlsplit(company.website).hostname
        if host and (host == "linkedin.com" or host.endswith(".linkedin.com")):
            self._warnings.append("LinkedIn URLs are manual-only; no automated research")
            return []
        pages = [company.website]
        result = []
        seen = set()
        for index in range(self.max_pages):
            if index >= len(pages):
                break
            url = pages[index]
            fetched = self.fetcher.fetch(url)
            if fetched.error_code:
                self._warnings.append(f"{url}: {fetched.error_code}")
                continue
            if urlsplit(fetched.final_url or url).hostname != host:
                self._warnings.append("External domain redirect rejected")
                continue
            if not fetched.content_type.startswith(("text/html", "application/xhtml+xml")):
                self._warnings.append(f"{url}: non-HTML content")
                continue
            if BeautifulSoupHtmlAnalyzer().analyze(fetched.body, url).access_blocked:
                self._warnings.append(f"{url}: access restriction")
                continue
            result.extend(self.extract(fetched.body, fetched.final_url or url, company))
            if index == 0:
                soup = BeautifulSoup(fetched.body, "html.parser")
                for anchor in soup.find_all("a", href=True):
                    href = str(anchor.get("href"))
                    target = urljoin(fetched.final_url or url, href)
                    parsed = urlsplit(target)
                    if (
                        parsed.hostname == host
                        and parsed.scheme in {"http", "https"}
                        and not parsed.query
                        and not parsed.fragment
                        and LINK_PATTERN.search(
                            parsed.path + " " + anchor.get_text(" ", strip=True)
                        )
                        and target not in pages
                    ):
                        pages.append(target)
        unique = []
        for c in result:
            key = (normalize_name(c.full_name), c.role_title, c.source_url)
            if key not in seen:
                unique.append(c)
                seen.add(key)
        return unique

    @staticmethod
    def extract(html: str, url: str, company: Company) -> list[ContactCandidate]:
        soup = BeautifulSoup(html, "html.parser")
        candidates = []
        # JSON-LD Person with explicit worksFor; never interpret organization fields as people.
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.get_text())
            except (ValueError, TypeError):
                continue
            nodes = data if isinstance(data, list) else [data]
            if isinstance(data, dict) and isinstance(data.get("@graph"), list):
                nodes = data["@graph"]
            for node in nodes:
                if not isinstance(node, dict) or node.get("@type") != "Person":
                    continue
                employer = node.get("worksFor", {})
                if not isinstance(employer, dict) or normalize_name(
                    str(employer.get("name", ""))
                ) != normalize_name(company.canonical_name):
                    continue
                name = node.get("name")
                title = node.get("jobTitle")
                if not isinstance(name, str) or not isinstance(title, str):
                    continue
                try:
                    candidates.append(
                        ContactCandidate(
                            full_name=name,
                            role_title=title,
                            role_category=category_for(title),
                            company_id=company.id,
                            company_name=company.canonical_name,
                            association_statement=f"{name}: {title} at {company.canonical_name}",
                            public_profile_url=node.get("url"),
                            public_email=node.get("email"),
                            public_phone=node.get("telephone"),
                            source_url=url,
                            source_type=SourceType.WEBSITE,
                            observed_at=utc_now(),
                            confidence=0.9,
                        )
                    )
                except ValidationError:
                    continue
        for element in soup.select("script,style,noscript,template,[hidden],[aria-hidden=true]"):
            element.decompose()
        for element in soup.find_all(["p", "li", "div", "td"]):
            # Parse leaf cards only; avoid pairing a name with a role in a distant section.
            if element.find(["p", "li", "div", "td"]):
                continue
            text = element.get_text(" ", strip=True)
            for pattern, category in ROLE_PATTERNS:
                match = re.fullmatch(
                    rf"(?P<role>(?i:{pattern}))\s*[:–—-]\s*(?P<name>{NAME_PATTERN})", text
                )
                if not match:
                    continue
                name = match["name"]
                title = match["role"]
                candidates.append(
                    ContactCandidate(
                        full_name=name,
                        role_title=title,
                        role_category=category,
                        company_id=company.id,
                        company_name=company.canonical_name,
                        association_statement=f"Official website states {text}",
                        source_url=url,
                        source_title=soup.title.get_text(strip=True) if soup.title else None,
                        source_type=SourceType.WEBSITE,
                        observed_at=utc_now(),
                        confidence=0.9,
                    )
                )
        return candidates
