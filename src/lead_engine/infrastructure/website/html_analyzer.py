"""Deterministic ES/EN observations in supplied static HTML, never link traversal."""

import re
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup
from bs4.element import Tag
from pydantic import JsonValue

from lead_engine.application.website import HtmlAnalysis
from lead_engine.domain.audit import Finding, FindingType
from lead_engine.domain.identity import normalize_name

BOOKING = (
    r"\b(reserva|reservar|reservas|reservation|reservations|book|booking|"
    r"appointments?|cita|citas|agenda|agendar)\b"
)
QUOTE = r"\b(cotiza|cotizar|cotizacion|cotizaciones|quote|quotation)\b"
CATALOG = r"\b(carta|menu|catalogo|catalog)\b"
PRODUCTS = r"\b(productos|products|producto|product)\b"
SERVICES = r"\b(servicios|services|servicio|service)\b"
ECOMMERCE = r"\b(carrito|cart|checkout|comprar|add to cart|anadir al carrito|pagar)\b"
PRIVACY = r"\b(privacidad|privacy|politica de privacidad)\b"
CONTACT_FORM_EXCLUSIONS = (
    r"\b(search|newsletter|subscribe|subscription|login|sign in|signin|register|signup|"
    r"account|feedback|survey|review|complaint|reclamo|encuesta|opinion|comentarios|resena)\b"
)
CONTACT_FORM_CONTEXT = r"\b(contact|contacto|contactar|contactanos)\b"
EDITORIAL_PATH_PREFIXES = {
    "blog",
    "blogs",
    "news",
    "article",
    "articles",
    "insights",
    "post",
    "posts",
}
BOOKING_NON_ACTION_PATHS = {
    "about",
    "about-us",
    "nosotros",
    "us",
    "help",
    "privacy",
    "terms",
    "careers",
}
PROVIDERS = (
    "opentable.com",
    "sevenrooms.com",
    "covermanager.com",
    "resy.com",
    "calendly.com",
    "booksy.com",
)
PROVIDER_SYSTEM_PATHS = {"about", "help", "privacy", "terms", "login", "signup", "careers"}
MAP_SYSTEM_PATHS = {"about", "help", "privacy", "terms", "login"}
DAY_NAME = (
    r"(?:monday|mon|tuesday|tue|wednesday|wed|thursday|thu|friday|fri|"
    r"saturday|sat|sunday|sun|lunes|lun|martes|mar|miercoles|miércoles|mie|"
    r"jueves|jue|viernes|vie|sabado|sábado|sab|domingo|dom)"
)
DAY_PATTERN = re.compile(rf"\b{DAY_NAME}\b", re.IGNORECASE)
DAY_RANGE_PATTERN = re.compile(rf"\b{DAY_NAME}\s*(?:a|hasta|to|-|–)\s*{DAY_NAME}\b", re.IGNORECASE)
TIME_RANGE_PATTERN = re.compile(
    r"\b\d{1,2}:\d{2}\s*(?:am|pm)?\s*(?:-|–|—|a|to)\s*"
    r"\d{1,2}:\d{2}\s*(?:am|pm)?\b",
    re.IGNORECASE,
)
HOURS_LABEL_PATTERN = re.compile(
    r"\b(?:horario(?:\s+de\s+atenci[oó]n)?|business\s+hours|hours)\b", re.IGNORECASE
)
EVENT_CONTEXT_PATTERN = re.compile(
    r"\b(?:evento|webinar|charla|reuni[oó]n|cita|conferencia|taller|lanzamiento|"
    r"entrega|event|meeting|conference|workshop|launch|delivery)\b",
    re.IGNORECASE,
)
PROVIDER_SCHEDULE_CONTEXT_PATTERN = re.compile(
    r"\b(?:dr|dra|doctor|doctora|medico|especialista|consultas?|citas?|"
    r"agenda medica|disponibilidad|physician|specialist|consultations?|"
    r"appointments?|availability)\b"
)
ADDRESS_LABELS = {"direccion", "ubicacion", "address", "location"}
ADDRESS_PATTERN = re.compile(
    r"\b(?:Av\.?|Avenida|Calle|Jr\.?|Jiron|Street|St\.?|Road|Rd\.?)\s+"
    r"[\w\s.,-]{2,60}?\b\d{1,5}\b|"
    r"\b\d{1,5}\s+[\w\s.,-]{1,50}\b(?:Street|St\.?|Road|Rd\.?)",
    re.IGNORECASE,
)


def is_host(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def is_concrete_social_link(host: str, path: str, domain: str) -> bool:
    return is_host(host, domain) and bool(path.strip("/"))


def is_editorial_path(path: str) -> bool:
    first_segment = path.strip("/").split("/", maxsplit=1)[0].casefold()
    return first_segment in EDITORIAL_PATH_PREFIXES


def is_actionable_booking_target(href: str, scheme: str, hostname: str | None, path: str) -> bool:
    raw_href = href.strip()
    if not raw_href or raw_href.startswith("#") or scheme.lower() not in {"http", "https"}:
        return False
    if not hostname or is_editorial_path(path):
        return False
    first_segment = path.strip("/").split("/", maxsplit=1)[0].casefold()
    return first_segment not in BOOKING_NON_ACTION_PATHS


def find_business_hours(soup: BeautifulSoup) -> str | None:
    elements = soup.find_all(["p", "div", "section", "article", "li"])
    for element in sorted(elements, key=lambda item: len(item.get_text(" ", strip=True))):
        text = element.get_text(" ", strip=True)
        if not text:
            continue
        context_element = element
        for parent in element.parents:
            if isinstance(parent, Tag) and parent.name in {"div", "section", "article", "li"}:
                context_element = parent
                break
        context = context_element.get_text(" ", strip=True)
        element_position = context.find(text)

        for label in HOURS_LABEL_PATTERN.finditer(text):
            window_end = min(len(text), label.end() + 100)
            schedule = re.search(
                rf"{DAY_PATTERN.pattern}[\s\S]{{0,40}}?{TIME_RANGE_PATTERN.pattern}",
                text[label.end() : window_end],
                re.IGNORECASE,
            )
            if schedule is None:
                continue
            schedule_text = schedule.group(0)
            context_position = context.find(schedule_text, max(element_position, 0))
            if context_position < 0:
                context_position = text.find(schedule_text)
                local_context = text[
                    max(0, context_position - 120) : context_position + len(schedule_text)
                ]
            else:
                local_context = context[
                    max(0, context_position - 120) : context_position + len(schedule_text)
                ]
            label_schedule_context = text[label.start() : label.end() + schedule.end()]
            if EVENT_CONTEXT_PATTERN.search(label_schedule_context) or (
                PROVIDER_SCHEDULE_CONTEXT_PATTERN.search(normalize_name(local_context))
            ):
                continue
            return schedule_text

        for day_range in DAY_RANGE_PATTERN.finditer(text):
            window_end = min(len(text), day_range.end() + 40)
            time_range = TIME_RANGE_PATTERN.search(text, day_range.end(), window_end)
            if time_range is None:
                continue
            schedule_text = text[day_range.start() : time_range.end()]
            context_position = context.find(schedule_text, max(element_position, 0))
            if context_position < 0:
                context_position = text.find(schedule_text)
                local_context = text[max(0, context_position - 80) : time_range.end() + 40]
            else:
                local_context = context[
                    max(0, context_position - 80) : context_position + len(schedule_text) + 40
                ]
            local_normalized = normalize_name(local_context)
            if EVENT_CONTEXT_PATTERN.search(
                local_context
            ) or PROVIDER_SCHEDULE_CONTEXT_PATTERN.search(local_normalized):
                continue
            return schedule_text
    return None


def find_labeled_address_evidence(soup: BeautifulSoup) -> str | None:
    for label in soup.find_all(["strong", "b", "span", "h2", "h3", "h4", "dt", "label"]):
        if normalize_name(label.get_text(" ", strip=True)) not in ADDRESS_LABELS:
            continue
        nearby_parts: list[str] = []
        for index, sibling in enumerate(label.next_siblings):
            if index >= 3:
                break
            if isinstance(sibling, Tag):
                nearby_parts.append(sibling.get_text(" ", strip=True))
            else:
                nearby_parts.append(str(sibling).strip())
        match = ADDRESS_PATTERN.search(" ".join(part for part in nearby_parts if part))
        if match:
            return match.group(0).strip()
    return None


def is_concrete_reservation_provider_link(host: str, path: str, domain: str) -> bool:
    resource_path = path.strip("/")
    if not is_host(host, domain) or not resource_path:
        return False
    first_segment = resource_path.split("/", maxsplit=1)[0].casefold()
    return first_segment not in PROVIDER_SYSTEM_PATHS


def has_nonempty_query_value(query: str, *keys: str) -> bool:
    parameters = parse_qs(query)
    return any(value.strip() for key in keys for value in parameters.get(key, []))


def is_concrete_map_link(host: str, path: str, query: str) -> bool:
    segments = [segment.casefold() for segment in path.strip("/").split("/") if segment]
    if not host or (segments and segments[0] in MAP_SYSTEM_PATHS):
        return False
    if len(segments) > 1 and segments[0] == "maps" and segments[1] in MAP_SYSTEM_PATHS:
        return False

    if is_host(host, "maps.app.goo.gl"):
        return len(segments) == 1
    if is_host(host, "maps.google.com"):
        if has_nonempty_query_value(query, "q", "query"):
            return True
        if not segments:
            return False
        return segments[0] != "maps" or (len(segments) > 1 and segments[1] != "search")
    if is_host(host, "google.com"):
        if not segments or segments[0] != "maps":
            return False
        if has_nonempty_query_value(query, "q", "query"):
            return True
        return len(segments) > 1 and segments[1] != "search"
    if is_host(host, "goo.gl"):
        return len(segments) > 1 and segments[0] == "maps"
    if is_host(host, "openstreetmap.org"):
        if has_nonempty_query_value(query, "mlat") and has_nonempty_query_value(query, "mlon"):
            return True
        return (
            len(segments) > 1
            and segments[0] in {"node", "way", "relation"}
            and segments[1].isdigit()
        )
    return False


def has_concrete_tel_target(href: str) -> bool:
    return href.lower().startswith("tel:") and re.search(r"\d", href[4:]) is not None


def has_concrete_mailto_target(href: str) -> bool:
    if not href.lower().startswith("mailto:"):
        return False
    address = urlsplit(href).path
    if address.count("@") != 1:
        return False
    local_part, domain = address.split("@")
    return bool(local_part and domain) and not any(char.isspace() for char in address)


def has_phone_query(query: str) -> bool:
    return any(re.search(r"\d", phone) for phone in parse_qs(query).get("phone", []))


def is_concrete_whatsapp_link(href: str, host: str, scheme: str, path: str, query: str) -> bool:
    if scheme == "whatsapp":
        target = urlsplit(href)
        return target.netloc.lower() == "send" and has_phone_query(query)
    if scheme not in {"http", "https"}:
        return False
    if is_host(host, "wa.me"):
        return re.fullmatch(r"/\+?\d+/?", path) is not None
    if is_host(host, "whatsapp.com"):
        return path.rstrip("/") == "/send" and has_phone_query(query)
    return False


class BeautifulSoupHtmlAnalyzer:
    def analyze(self, html: str, page_url: str) -> HtmlAnalysis:
        soup = BeautifulSoup(html, "html.parser")
        if soup.find() is None:
            return HtmlAnalysis(warnings=("No HTML elements found; static signals not inferred",))
        title = soup.title.get_text(" ", strip=True) if soup.title else None
        page_text = normalize_name(soup.get_text(" ", strip=True))
        challenge_title = normalize_name(title or "")
        if (
            soup.select_one('form input[type="password"]') is not None
            or any(
                text in challenge_title
                for text in ("just a moment", "access denied", "verify you are human", "captcha")
            )
            or ("verify you are human" in page_text and len(page_text) < 1000)
        ):
            return HtmlAnalysis(
                title=title,
                access_blocked=True,
                warnings=("Login/password or access challenge markup detected",),
            )
        for tag in soup.select('script, style, template, noscript, [hidden], [aria-hidden="true"]'):
            tag.decompose()
        findings: dict[FindingType, Finding] = {}

        def add(
            kind: FindingType, statement: str, value: JsonValue = True, confidence: float = 1
        ) -> None:
            if kind not in findings:
                findings[kind] = Finding(
                    kind=kind, statement=statement, value=value, confidence=confidence
                )

        if title:
            add(FindingType.PAGE_TITLE_PRESENT, "HTML title element contains text", title[:1000])
        description_tag = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
        if description_tag and str(description_tag.get("content", "")).strip():
            add(
                FindingType.META_DESCRIPTION_PRESENT,
                "HTML meta description contains text",
                str(description_tag.get("content"))[:1000],
            )
        viewport = soup.find("meta", attrs={"name": re.compile("^viewport$", re.I)})
        if viewport and str(viewport.get("content", "")).strip():
            add(
                FindingType.MOBILE_VIEWPORT_PRESENT,
                "HTML viewport meta tag contains a value",
                str(viewport.get("content"))[:1000],
            )
        h1s = soup.find_all("h1")
        if h1s:
            add(FindingType.H1_PRESENT, "HTML h1 elements were observed", len(h1s))
        if len(h1s) > 1:
            add(FindingType.MULTIPLE_H1, "HTML contains more than one h1 element", len(h1s))
        for element in soup.find_all(["a", "button", "input", "iframe"]):
            if element.name == "input" and str(element.get("type", "")).lower() not in {
                "submit",
                "button",
            }:
                continue
            href = str(element.get("href") or element.get("src") or "")
            text = element.get_text(" ", strip=True) + " " + str(element.get("value", ""))
            text += " " + str(element.get("aria-label", ""))
            # Use action text/path, not domain words (e.g. facebook must not match 'book').
            target = urlsplit(urljoin(page_url, href))
            host = (target.hostname or "").lower().rstrip(".")
            action = normalize_name(text + " " + (target.path if href else ""))
            booking_action = text
            if element.name == "a" and href:
                booking_action = (
                    ""
                    if (
                        not is_actionable_booking_target(
                            href, target.scheme, target.hostname, target.path
                        )
                        or is_concrete_whatsapp_link(
                            href, host, target.scheme, target.path, target.query
                        )
                    )
                    else booking_action + " " + target.path
                )
            elif element.name == "a":
                booking_action = ""
            booking_action = normalize_name(booking_action)
            quote_action = text
            if element.name == "a" and href:
                quote_action = (
                    "" if is_editorial_path(target.path) else quote_action + " " + target.path
                )
            quote_action = normalize_name(quote_action)
            discovery_action = (
                "" if element.name == "a" and href and is_editorial_path(target.path) else action
            )
            if has_concrete_tel_target(href):
                add(FindingType.PHONE_LINK_PRESENT, "HTML contains a tel link", href[:1000])
            if has_concrete_mailto_target(href):
                add(FindingType.EMAIL_LINK_PRESENT, "HTML contains a mailto link", href[:1000])
            if is_concrete_whatsapp_link(href, host, target.scheme, target.path, target.query):
                add(FindingType.WHATSAPP_LINK_PRESENT, "HTML contains a WhatsApp link", href[:1000])
            for domain, kind in (
                ("instagram.com", FindingType.INSTAGRAM_LINK_PRESENT),
                ("facebook.com", FindingType.FACEBOOK_LINK_PRESENT),
                ("linkedin.com", FindingType.LINKEDIN_LINK_PRESENT),
            ):
                if (
                    href
                    and target.scheme in {"http", "https"}
                    and is_concrete_social_link(host, target.path, domain)
                ):
                    add(kind, "HTML contains a link to a recognized social platform", href[:1000])
                    add(
                        FindingType.SOCIAL_LINK_PRESENT,
                        "HTML links to at least one recognized social platform",
                    )
            if (
                href
                and target.scheme in {"http", "https"}
                and is_concrete_map_link(host, target.path, target.query)
            ):
                add(
                    FindingType.MAP_LINK_PRESENT, "HTML contains a recognized map link", href[:1000]
                )
            if any(
                is_concrete_reservation_provider_link(host, target.path, provider)
                for provider in PROVIDERS
            ):
                add(
                    FindingType.RESERVATION_PROVIDER_PRESENT,
                    "HTML references a recognized reservation provider",
                    href[:1000],
                )
            for pattern, kind, description in (
                (
                    BOOKING,
                    FindingType.BOOKING_CTA_PRESENT,
                    "CTA text or non-editorial path matches an ES/EN booking keyword",
                ),
                (
                    QUOTE,
                    FindingType.QUOTE_CTA_PRESENT,
                    "CTA text or non-editorial path matches an ES/EN quote keyword",
                ),
                (
                    ECOMMERCE,
                    FindingType.ECOMMERCE_PRESENT,
                    "Action text/path matches a cart/checkout/purchase keyword",
                ),
            ):
                candidate = action
                if kind == FindingType.BOOKING_CTA_PRESENT:
                    candidate = booking_action
                elif kind == FindingType.QUOTE_CTA_PRESENT:
                    candidate = quote_action
                if element.name != "iframe" and re.search(pattern, candidate):
                    add(kind, description, {"text": text[:500], "href": href[:500]}, 0.9)
            # Page/path signals require a link, not an incidental word in a paragraph/button.
            if element.name == "a" and href and target.scheme in {"http", "https"}:
                for pattern, kind, description in (
                    (
                        CATALOG,
                        FindingType.CATALOG_PRESENT,
                        "Link text/path matches a catalog/menu keyword",
                    ),
                    (
                        PRODUCTS,
                        FindingType.PRODUCTS_PAGE_PRESENT,
                        "Link text/path matches a products keyword",
                    ),
                    (
                        SERVICES,
                        FindingType.SERVICES_PAGE_PRESENT,
                        "Link text/path matches a services keyword",
                    ),
                    (
                        PRIVACY,
                        FindingType.PRIVACY_POLICY_PRESENT,
                        "Link text/path matches a privacy keyword",
                    ),
                ):
                    candidate = (
                        discovery_action
                        if kind
                        in {
                            FindingType.CATALOG_PRESENT,
                            FindingType.PRODUCTS_PAGE_PRESENT,
                            FindingType.SERVICES_PAGE_PRESENT,
                        }
                        else action
                    )
                    if re.search(pattern, candidate):
                        add(kind, description, {"text": text[:500], "href": href[:500]}, 0.9)
        for form in soup.find_all("form"):
            # Use only this form's visible context; conflicting purposes take precedence.
            controls = form.find_all(["input", "textarea"])
            has_message = form.find("textarea") is not None
            has_contact = any(
                str(control.get("type", "")).lower() in {"email", "tel"} for control in controls
            )
            form_context = normalize_name(
                str(form.get("id", ""))
                + " "
                + str(form.get("action", ""))
                + " "
                + form.get_text(" ", strip=True)
            )
            has_conflicting_purpose = re.search(CONTACT_FORM_EXCLUSIONS, form_context) is not None
            has_contact_context = re.search(CONTACT_FORM_CONTEXT, form_context) is not None
            if has_message and not has_conflicting_purpose and (has_contact or has_contact_context):
                add(
                    FindingType.CONTACT_FORM_PRESENT,
                    "Form markup has a message field and commercial contact context",
                    str(form.get("action", ""))[:1000],
                    0.9,
                )
        address = soup.find("address")
        if address and address.get_text(strip=True):
            add(
                FindingType.ADDRESS_PRESENT,
                "HTML address element contains text",
                address.get_text(" ", strip=True)[:1000],
            )
        else:
            address_text = find_labeled_address_evidence(soup)
            if address_text:
                add(
                    FindingType.ADDRESS_PRESENT,
                    "Address-like text appears beside an explicit address/location label",
                    address_text[:1000],
                    0.7,
                )
        hours = find_business_hours(soup)
        if hours:
            add(
                FindingType.BUSINESS_HOURS_PRESENT,
                "Visible text is consistent with a business-hours schedule",
                hours[:1000],
                0.7,
            )
        kinds = findings.keys()
        derived = (
            (
                FindingType.HAS_DIRECT_CONTACT_PATH,
                {
                    FindingType.PHONE_LINK_PRESENT,
                    FindingType.EMAIL_LINK_PRESENT,
                    FindingType.WHATSAPP_LINK_PRESENT,
                    FindingType.CONTACT_FORM_PRESENT,
                },
            ),
            (
                FindingType.HAS_RESERVATION_PATH,
                {FindingType.BOOKING_CTA_PRESENT, FindingType.RESERVATION_PROVIDER_PRESENT},
            ),
            (FindingType.HAS_QUOTE_PATH, {FindingType.QUOTE_CTA_PRESENT}),
            (
                FindingType.HAS_PRODUCT_DISCOVERY_PATH,
                {FindingType.PRODUCTS_PAGE_PRESENT, FindingType.CATALOG_PRESENT},
            ),
            (FindingType.HAS_SERVICE_DISCOVERY_PATH, {FindingType.SERVICES_PAGE_PRESENT}),
            (
                FindingType.HAS_CONVERSION_CTA,
                {
                    FindingType.BOOKING_CTA_PRESENT,
                    FindingType.QUOTE_CTA_PRESENT,
                    FindingType.ECOMMERCE_PRESENT,
                },
            ),
        )
        for kind, inputs in derived:
            matched = sorted(item.value for item in kinds & inputs)
            if matched:
                matched_values: list[JsonValue] = list(matched)
                add(
                    kind,
                    "Deterministic aggregate of observed markup signals",
                    {"based_on": matched_values},
                    0.9,
                )
        return HtmlAnalysis(title=title, findings=tuple(findings.values()))
