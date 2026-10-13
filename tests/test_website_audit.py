import json
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import Engine
from typer.testing import CliRunner

from lead_engine.application.auditor import WebsiteAuditService
from lead_engine.application.services import LeadService
from lead_engine.application.website import AuditSettings
from lead_engine.cli.main import app
from lead_engine.domain.audit import AuditStatus, FindingType, WebsiteAudit
from lead_engine.domain.enums import CampaignType
from lead_engine.domain.models import Campaign, Company, Evidence, Lead, Source
from lead_engine.infrastructure.repositories import SqlAlchemyUnitOfWork
from lead_engine.infrastructure.website.html_analyzer import BeautifulSoupHtmlAnalyzer
from lead_engine.infrastructure.website.http_fetcher import HttpxFetcher
from lead_engine.infrastructure.website.security import PublicUrlPolicy

FIXTURES = Path(__file__).parent / "fixtures" / "website"


def public_dns(host: str, port: int) -> Sequence[str]:
    return ["93.184.216.34"]


def fetcher_for(html: str, *, status: int = 200, https_redirect: bool = False) -> HttpxFetcher:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if https_redirect and request.url.scheme == "http":
            return httpx.Response(301, headers={"Location": "https://site.example/"})
        return httpx.Response(status, text=html, headers={"Content-Type": "text/html"})

    return HttpxFetcher(
        AuditSettings(min_request_interval_seconds=0),
        policy=PublicUrlPolicy(public_dns),
        transport=httpx.MockTransport(handler),
    )


def company(engine: Engine, website: str | None = "https://site.example/") -> Company:
    with SqlAlchemyUnitOfWork(engine) as uow:
        value = LeadService(uow).upsert_company(Company(canonical_name="Demo", website=website))
        uow.commit()
        return value


def service(
    engine: Engine, fetcher: HttpxFetcher, settings: AuditSettings | None = None
) -> WebsiteAuditService:
    return WebsiteAuditService(
        lambda: SqlAlchemyUnitOfWork(engine), fetcher, BeautifulSoupHtmlAnalyzer(), settings
    )


def test_booking_signals_es_en_and_metadata() -> None:
    analysis = BeautifulSoupHtmlAnalyzer().analyze(
        (FIXTURES / "booking.html").read_text(encoding="utf-8"), "https://site.example/"
    )
    kinds = {value.kind for value in analysis.findings}
    assert {
        FindingType.PAGE_TITLE_PRESENT,
        FindingType.META_DESCRIPTION_PRESENT,
        FindingType.MOBILE_VIEWPORT_PRESENT,
        FindingType.H1_PRESENT,
        FindingType.MULTIPLE_H1,
        FindingType.BOOKING_CTA_PRESENT,
        FindingType.QUOTE_CTA_PRESENT,
        FindingType.SERVICES_PAGE_PRESENT,
        FindingType.PRIVACY_POLICY_PRESENT,
        FindingType.RESERVATION_PROVIDER_PRESENT,
        FindingType.ADDRESS_PRESENT,
        FindingType.BUSINESS_HOURS_PRESENT,
        FindingType.HAS_RESERVATION_PATH,
        FindingType.HAS_CONVERSION_CTA,
    } <= kinds
    english = BeautifulSoupHtmlAnalyzer().analyze(
        '<a href="/book">Book now</a><a href="/quote">Request a quote</a>', "https://site.example"
    )
    assert {FindingType.BOOKING_CTA_PRESENT, FindingType.QUOTE_CTA_PRESENT} <= {
        item.kind for item in english.findings
    }


@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        ('<a href="/reservar">Reservar</a>', True),
        ('<a href="/reservar">Reservar mesa</a>', True),
        ("<button>Reservar cita</button>", True),
        ("<button>Agendar</button>", True),
        ("<button>Book now</button>", True),
        ('<a href="/booking">Book appointment</a>', True),
        ('<input type="submit" value="Reservar">', True),
        ('<a href="/reservar">Ver disponibilidad</a>', True),
        ('<a href="/booking">Continue</a>', True),
        ('<a href="/appointments/book">Continue</a>', True),
        ('<a href="/blog/how-to-book-a-table">Continue</a>', False),
        ('<a href="/blog/booking-trends">Continue</a>', False),
        ('<a href="/news/reservas-en-restaurantes">Continue</a>', False),
        ('<a href="/article/agenda-digital">Continue</a>', False),
        ('<a href="/articles/how-to-book">Continue</a>', False),
        ('<a href="/insights/booking-technology">Continue</a>', False),
        ('<a href="/post/how-to-book">Continue</a>', False),
        ('<a href="/posts/reservation-tips">Continue</a>', False),
        ('<a href="/blog/booking-guide">Read our booking guide</a>', False),
        ('<a href="/articles/booking-tips">Booking tips</a>', False),
        ('<a href="/insights/reservation-guide">Reservation guide</a>', False),
        ('<a href="/blog/reservar">Book now</a>', False),
        ('<a href="/booking">Book now</a>', True),
        ('<a href="/reservar">Reserva ahora</a>', True),
    ],
)
def test_booking_cta_excludes_editorial_links(markup: str, expected: bool) -> None:
    analysis = BeautifulSoupHtmlAnalyzer().analyze(markup, "https://site.example/")
    kinds = {item.kind for item in analysis.findings}
    assert (FindingType.BOOKING_CTA_PRESENT in kinds) is expected
    assert (FindingType.HAS_RESERVATION_PATH in kinds) is expected


@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        ('<a href="/reservar">Reserva ahora</a>', True),
        ('<a href="/reservar/consulta">Reservar consulta</a>', True),
        ('<a href="/booking">Book now</a>', True),
        ('<a href="/booking/appointment">Book appointment</a>', True),
        ('<a href="/citas">Solicitar cita</a>', True),
        ('<a href="/agenda">Agendar</a>', True),
        ('<a href="/contacto">Solicitar cita</a>', True),
        ('<a href="/contact">Book now</a>', True),
        ('<a href="https://booking.example/reserve">Reserva</a>', True),
        ('<a href="javascript:void(0)">Haz una reserva</a>', False),
        ('<a href="javascript:;">Reservar</a>', False),
        ('<a href="#">Reservar</a>', False),
        ('<a href="#reservas">Reservar</a>', False),
        ('<a href="data:text/plain,reserva">Reservar</a>', False),
        ('<a href="mailto:reservas@example.com">Reservar</a>', False),
        ('<a href="tel:+51123456789">Reservar</a>', False),
        ('<a href="https://wa.me/51123456789">Reservar</a>', False),
        ('<a href="">Reservar</a>', False),
        ('<a href="/about-us/">Solicitar cita</a>', False),
        ('<a href="/about">Book now</a>', False),
        ('<a href="/nosotros">Reservar</a>', False),
        ('<a href="/help">Reservar</a>', False),
        ('<a href="/privacy">Book now</a>', False),
        ('<a href="/terms">Reservar</a>', False),
        ('<a href="/careers">Solicitar cita</a>', False),
        ("<button>Reservar</button>", True),
        ('<input type="submit" value="Agendar">', True),
    ],
)
def test_booking_cta_requires_actionable_anchor_destination(markup: str, expected: bool) -> None:
    analysis = BeautifulSoupHtmlAnalyzer().analyze(markup, "https://site.example/")
    kinds = {item.kind for item in analysis.findings}
    assert (FindingType.BOOKING_CTA_PRESENT in kinds) is expected
    assert (FindingType.HAS_RESERVATION_PATH in kinds) is expected


@pytest.mark.parametrize(
    "html",
    [
        "<address>Av. Balta 123, Chiclayo</address>",
        "<div><strong>Dirección</strong><span>Av. Balta 123, Chiclayo</span></div>",
        "<div><strong>Direccion</strong><span>Calle Los Pinos 456</span></div>",
        "<section><h3>Ubicación</h3><p>Jr. Lima 789</p></section>",
        "<div><span>Address</span><p>123 Main Street</p></div>",
        "<div><span>Location</span><p>45 Oak Road</p></div>",
        "<h1>Centro Médico Chiclayo</h1><div><strong>Dirección</strong>"
        "<p>Av. Balta 456, Chiclayo</p></div>",
    ],
)
def test_address_evidence_requires_semantic_or_labeled_context(html: str) -> None:
    analysis = BeautifulSoupHtmlAnalyzer().analyze(html, "https://site.example/")
    finding = next(item for item in analysis.findings if item.kind == FindingType.ADDRESS_PRESENT)
    assert isinstance(finding.value, str)
    assert finding.value


@pytest.mark.parametrize(
    "html",
    [
        "<p>Nuestra sede de Miraflores está en Av. Pardo 123.</p>",
        "<p>Av. Pardo 123 corresponde a otra sucursal.</p>",
        "<div><h2>Nuestras sedes</h2><p>Miraflores: Av. Pardo 123, Lima</p>"
        "<p>San Isidro: Calle Las Begonias 456, Lima</p></div>",
        "<footer>Av. Pardo 123, Miraflores, Lima</footer>",
        "<div hidden><address>Av. Balta 123, Chiclayo</address></div>",
        '<div aria-hidden="true"><address>Calle Lima 123</address></div>',
        "<script>Av. Pardo 123</script><template>Calle Lima 456</template>",
        "<h1>Centro Médico Chiclayo</h1><p>También atendemos en Miraflores: "
        "Av. Pardo 123, Lima.</p>",
    ],
)
def test_address_evidence_ignores_unlabeled_or_hidden_locations(html: str) -> None:
    analysis = BeautifulSoupHtmlAnalyzer().analyze(html, "https://site.example/")
    assert FindingType.ADDRESS_PRESENT not in {item.kind for item in analysis.findings}


@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        ('<a href="/cotizar">Cotizar</a>', True),
        ('<a href="/cotizacion">Solicitar cotización</a>', True),
        ("<button>Cotizar ahora</button>", True),
        ("<button>Solicita una cotización</button>", True),
        ("<button>Request a quote</button>", True),
        ('<a href="/quote">Get a quote</a>', True),
        ('<input type="submit" value="Cotizar">', True),
        ('<a href="/cotizar">Continuar</a>', True),
        ('<a href="/cotizacion">Ver formulario</a>', True),
        ('<a href="/quote">Continue</a>', True),
        ('<a href="/request-quote">Continue</a>', True),
        ('<a href="/blog/como-cotizar-un-proyecto">Continue</a>', False),
        ('<a href="/blog/cotizacion-materiales">Continue</a>', False),
        ('<a href="/news/nueva-politica-de-cotizaciones">Continue</a>', False),
        ('<a href="/news/cotizaciones">Continue</a>', False),
        ('<a href="/article/request-a-quote-best-practices">Continue</a>', False),
        ('<a href="/insights/quotation-process">Continue</a>', False),
        ('<a href="/posts/how-to-quote-projects">Continue</a>', False),
        ('<a href="/blog/guia-cotizaciones">Guía de cotizaciones</a>', False),
        ('<a href="/article/quote-guide">Quote guide</a>', False),
        ('<a href="/insights/quotation-tips">Quotation tips</a>', False),
        ('<a href="/articles/request-quote">Learn how to request a quote</a>', False),
        ('<a href="/blog/cotizar">Cotizar ahora</a>', False),
        ('<a href="/booking">Book now</a>', False),
        ("<button>Add to cart</button>", False),
    ],
)
def test_quote_cta_excludes_editorial_links(markup: str, expected: bool) -> None:
    analysis = BeautifulSoupHtmlAnalyzer().analyze(markup, "https://site.example/")
    kinds = {item.kind for item in analysis.findings}
    assert (FindingType.QUOTE_CTA_PRESENT in kinds) is expected
    assert (FindingType.HAS_QUOTE_PATH in kinds) is expected


@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        ('<a href="/catalogo">Catálogo</a>', {FindingType.CATALOG_PRESENT}),
        ('<a href="/catalog">Catalog</a>', {FindingType.CATALOG_PRESENT}),
        ('<a href="/menu">Menú</a>', {FindingType.CATALOG_PRESENT}),
        ('<a href="/carta">Carta</a>', {FindingType.CATALOG_PRESENT}),
        ('<a href="/catalogo">Ver más</a>', {FindingType.CATALOG_PRESENT}),
        ('<a href="/productos">Productos</a>', {FindingType.PRODUCTS_PAGE_PRESENT}),
        ('<a href="/products">Products</a>', {FindingType.PRODUCTS_PAGE_PRESENT}),
        (
            '<a href="/productos/ladrillos">Ladrillos</a>',
            {FindingType.PRODUCTS_PAGE_PRESENT},
        ),
        ('<a href="/products/bricks">Bricks</a>', {FindingType.PRODUCTS_PAGE_PRESENT}),
        ('<a href="/products">Explore</a>', {FindingType.PRODUCTS_PAGE_PRESENT}),
        ('<a href="/productos">Conoce más</a>', {FindingType.PRODUCTS_PAGE_PRESENT}),
        ('<a href="/blog/catalogo-digital-2026">Continue</a>', set()),
        ('<a href="/blog/productos-innovadores">Continue</a>', set()),
        ('<a href="/news/nuevos-productos">Continue</a>', set()),
        ('<a href="/article/product-strategy">Continue</a>', set()),
        ('<a href="/articles/catalog-best-practices">Continue</a>', set()),
        ('<a href="/insights/product-trends">Continue</a>', set()),
        ('<a href="/posts/catalog-design">Continue</a>', set()),
        ('<a href="/blog/guia-productos">Guía de productos</a>', set()),
        ('<a href="/insights/tendencias-catalogo">Tendencias de catálogo</a>', set()),
        ('<a href="/article/product-trends">Product trends</a>', set()),
        ('<a href="/articles/catalog-guide">Catalog guide</a>', set()),
        ('<a href="/blog/productos">Productos</a>', set()),
        ('<a href="/article/catalog">Catalog</a>', set()),
    ],
)
def test_catalog_and_product_links_exclude_editorial_content(
    markup: str, expected: set[FindingType]
) -> None:
    analysis = BeautifulSoupHtmlAnalyzer().analyze(markup, "https://site.example/")
    kinds = {item.kind for item in analysis.findings}
    discovery_findings = {FindingType.CATALOG_PRESENT, FindingType.PRODUCTS_PAGE_PRESENT}
    assert kinds & discovery_findings == expected
    assert (FindingType.HAS_PRODUCT_DISCOVERY_PATH in kinds) is bool(expected)


def test_services_booking_and_quote_signals_do_not_become_product_discovery() -> None:
    analysis = BeautifulSoupHtmlAnalyzer().analyze(
        '<a href="/servicios">Servicios</a><a href="/booking">Book now</a>'
        '<a href="/cotizar">Cotizar</a>',
        "https://site.example/",
    )
    kinds = {item.kind for item in analysis.findings}
    assert FindingType.SERVICES_PAGE_PRESENT in kinds
    assert FindingType.BOOKING_CTA_PRESENT in kinds
    assert FindingType.QUOTE_CTA_PRESENT in kinds
    assert FindingType.CATALOG_PRESENT not in kinds
    assert FindingType.PRODUCTS_PAGE_PRESENT not in kinds
    assert FindingType.HAS_PRODUCT_DISCOVERY_PATH not in kinds


@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        ('<a href="/servicios">Servicios</a>', True),
        ('<a href="/services">Services</a>', True),
        ('<a href="/servicio">Servicio</a>', True),
        ('<a href="/service">Service</a>', True),
        ('<a href="/servicios/cirugia">Cirugía</a>', True),
        ('<a href="/services/consulting">Consulting</a>', True),
        ('<a href="/servicios">Ver más</a>', True),
        ('<a href="/services">Explore</a>', True),
        ('<a href="/blog/servicios-digitales">Continue</a>', False),
        ('<a href="/news/nuevos-servicios">Continue</a>', False),
        ('<a href="/article/service-design">Continue</a>', False),
        ('<a href="/articles/services-strategy">Continue</a>', False),
        ('<a href="/insights/service-experience">Continue</a>', False),
        ('<a href="/posts/service-trends">Continue</a>', False),
        ('<a href="/blog/guia-servicios">Guía de servicios</a>', False),
        ('<a href="/insights/service-trends">Service trends</a>', False),
        ('<a href="/articles/services-guide">Services guide</a>', False),
        ('<a href="/blog/servicio">Cómo mejorar el servicio</a>', False),
        ('<a href="/blog/servicios">Servicios</a>', False),
        ('<a href="/article/services">Services</a>', False),
        ('<a href="/productos">Productos</a>', False),
        ('<a href="/catalogo">Catálogo</a>', False),
        ('<a href="/booking">Book now</a>', False),
        ('<a href="/cotizar">Cotizar</a>', False),
    ],
)
def test_service_links_exclude_editorial_destinations(markup: str, expected: bool) -> None:
    analysis = BeautifulSoupHtmlAnalyzer().analyze(markup, "https://site.example/")
    kinds = {item.kind for item in analysis.findings}
    assert (FindingType.SERVICES_PAGE_PRESENT in kinds) is expected
    assert (FindingType.HAS_SERVICE_DISCOVERY_PATH in kinds) is expected


def test_contact_and_catalog_signals() -> None:
    analyzer = BeautifulSoupHtmlAnalyzer()
    contact = analyzer.analyze(
        (FIXTURES / "contact.html").read_text(encoding="utf-8"), "https://site.example/"
    )
    assert {
        FindingType.PHONE_LINK_PRESENT,
        FindingType.EMAIL_LINK_PRESENT,
        FindingType.WHATSAPP_LINK_PRESENT,
        FindingType.CONTACT_FORM_PRESENT,
        FindingType.INSTAGRAM_LINK_PRESENT,
        FindingType.FACEBOOK_LINK_PRESENT,
        FindingType.LINKEDIN_LINK_PRESENT,
        FindingType.SOCIAL_LINK_PRESENT,
        FindingType.MAP_LINK_PRESENT,
        FindingType.HAS_DIRECT_CONTACT_PATH,
    } <= {item.kind for item in contact.findings}
    catalog = analyzer.analyze(
        (FIXTURES / "catalog.html").read_text(encoding="utf-8"), "https://site.example/"
    )
    assert {
        FindingType.CATALOG_PRESENT,
        FindingType.PRODUCTS_PAGE_PRESENT,
        FindingType.ECOMMERCE_PRESENT,
        FindingType.HAS_PRODUCT_DISCOVERY_PATH,
    } <= {item.kind for item in catalog.findings}


@pytest.mark.parametrize(
    ("form_markup", "expected"),
    [
        ('<form><input type="email"><textarea></textarea></form>', True),
        ('<form><input type="tel"><textarea></textarea></form>', True),
        ('<form id="contact"><textarea></textarea></form>', True),
        ('<form action="/contact"><textarea></textarea></form>', True),
        ('<form id="contacto"><textarea></textarea></form>', True),
        ("<form><textarea></textarea><button>Cont&aacute;ctanos</button></form>", True),
        ("<form><textarea></textarea><button>Contact us</button></form>", True),
        ('<form action="/search"><input type="email"><textarea></textarea></form>', False),
        ('<form action="/newsletter"><input type="email"><button>Subscribe</button></form>', False),
        ('<form action="/subscription"><input type="email"><textarea></textarea></form>', False),
        ('<form id="login"><input type="email"><textarea></textarea></form>', False),
        ('<form action="/register"><input type="email"><textarea></textarea></form>', False),
        ('<form id="signup"><input type="email"><textarea></textarea></form>', False),
        ('<form action="/feedback"><input type="email"><textarea></textarea></form>', False),
        ('<form id="survey"><input type="tel"><textarea></textarea></form>', False),
        ('<form action="/review"><input type="email"><textarea></textarea></form>', False),
        ('<form id="complaint"><input type="email"><textarea></textarea></form>', False),
        ('<form id="reclamo"><input type="email"><textarea></textarea></form>', False),
        ('<form id="encuesta"><input type="email"><textarea></textarea></form>', False),
        ('<form id="opinion"><input type="email"><textarea></textarea></form>', False),
        ('<form id="comentarios"><input type="email"><textarea></textarea></form>', False),
        ('<form id="resena"><input type="email"><textarea></textarea></form>', False),
        ('<form id="rese&ntilde;a"><input type="email"><textarea></textarea></form>', False),
        ('<form id="opini&oacute;n"><input type="email"><textarea></textarea></form>', False),
        ('<form><input type="email"></form>', False),
        (
            '<form id="contact" action="/feedback"><input type="email">'
            "<textarea></textarea></form>",
            False,
        ),
        ('<form id="contact"><input type="email"><textarea></textarea></form>', True),
        ('<form id="newsletter"><input type="email"><textarea hidden></textarea></form>', False),
        ('<form action="/booking"><input type="email"></form>', False),
    ],
)
def test_contact_form_requires_message_and_commercial_context(
    form_markup: str, expected: bool
) -> None:
    page_context = "<p>Contact our team</p>" if 'action="/search"' in form_markup else ""
    analysis = BeautifulSoupHtmlAnalyzer().analyze(
        page_context + form_markup, "https://site.example/"
    )
    kinds = {item.kind for item in analysis.findings}
    assert (FindingType.CONTACT_FORM_PRESENT in kinds) is expected
    assert (FindingType.HAS_DIRECT_CONTACT_PATH in kinds) is expected


def test_no_false_book_from_facebook_and_no_search_form_or_hidden_signals() -> None:
    analysis = BeautifulSoupHtmlAnalyzer().analyze(
        '<a href="https://facebook.com/demo">Facebook</a>'
        '<script>Reservar cotizar</script><p>Our book collection</p><a hidden href="/book">Book</a>'
        '<form><input type="search"></form><a href="https://instagram.com.evil.example">Social</a>',
        "https://site.example",
    )
    kinds = {item.kind for item in analysis.findings}
    assert FindingType.FACEBOOK_LINK_PRESENT in kinds
    assert (
        not {
            FindingType.BOOKING_CTA_PRESENT,
            FindingType.CONTACT_FORM_PRESENT,
            FindingType.INSTAGRAM_LINK_PRESENT,
        }
        & kinds
    )


@pytest.mark.parametrize(
    ("href", "expected"),
    [
        ("tel:", None),
        ("tel:+", None),
        ("tel:   ", None),
        ("tel:+51987654321", FindingType.PHONE_LINK_PRESENT),
        ("tel:987654321", FindingType.PHONE_LINK_PRESENT),
        ("tel:+1-407-555-1212", FindingType.PHONE_LINK_PRESENT),
        ("mailto:", None),
        ("mailto:?subject=Hello", None),
        ("mailto:contact", None),
        ("mailto:@example.com", None),
        ("mailto:ventas@example.com", FindingType.EMAIL_LINK_PRESENT),
        ("mailto:info@clinic.example?subject=Cita", FindingType.EMAIL_LINK_PRESENT),
        ("https://wa.me/", None),
        ("https://wa.me", None),
        ("https://wa.me/?text=Hola", None),
        ("https://wa.me/#footer", None),
        ("https://www.whatsapp.com/", None),
        ("https://whatsapp.com/", None),
        ("https://whatsapp.com/?ref=footer", None),
        ("https://www.whatsapp.com/#footer", None),
        ("https://wa.me/51987654321", FindingType.WHATSAPP_LINK_PRESENT),
        (
            "https://api.whatsapp.com/send?phone=51987654321",
            FindingType.WHATSAPP_LINK_PRESENT,
        ),
        (
            "https://www.whatsapp.com/send?phone=51987654321",
            FindingType.WHATSAPP_LINK_PRESENT,
        ),
        ("whatsapp://send?phone=51987654321", FindingType.WHATSAPP_LINK_PRESENT),
        ("https://www.whatsapp.com/download", None),
        ("https://www.whatsapp.com/features", None),
        ("https://www.whatsapp.com/security", None),
        ("https://wa.me.evil.example/51987654321", None),
        (
            "https://whatsapp.com.evil.example/send?phone=51987654321",
            None,
        ),
    ],
)
def test_direct_contact_links_require_concrete_targets(
    href: str, expected: FindingType | None
) -> None:
    analysis = BeautifulSoupHtmlAnalyzer().analyze(
        f'<a href="{href}">Contact</a>', "https://site.example/"
    )
    direct_contact = {
        FindingType.PHONE_LINK_PRESENT,
        FindingType.EMAIL_LINK_PRESENT,
        FindingType.WHATSAPP_LINK_PRESENT,
    }
    found = {item.kind for item in analysis.findings} & direct_contact
    assert found == ({expected} if expected else set())
    assert (FindingType.HAS_DIRECT_CONTACT_PATH in {item.kind for item in analysis.findings}) == (
        expected is not None
    )


@pytest.mark.parametrize(
    ("href", "expected"),
    [
        ("https://opentable.com/", False),
        ("https://www.opentable.com/", False),
        ("https://sevenrooms.com/", False),
        ("https://www.sevenrooms.com/", False),
        ("https://covermanager.com/", False),
        ("https://www.covermanager.com/", False),
        ("https://resy.com/", False),
        ("https://www.resy.com/", False),
        ("https://calendly.com/", False),
        ("https://www.calendly.com/", False),
        ("https://booksy.com/", False),
        ("https://www.booksy.com/", False),
        ("https://calendly.com/?utm_source=footer", False),
        ("https://resy.com/#booking", False),
        ("https://opentable.com/?ref=footer", False),
        ("https://calendly.com/demo", True),
        ("https://calendly.com/demo/30min", True),
        ("https://www.opentable.com/r/demo-restaurant", True),
        ("https://resy.com/cities/lima/venues/demo", True),
        ("https://www.sevenrooms.com/reservations/demo", True),
        ("https://www.covermanager.com/reservation/demo", True),
        ("https://booksy.com/en-us/demo", True),
        ("https://calendly.com/about", False),
        ("https://resy.com/help", False),
        ("https://booksy.com/privacy", False),
        ("https://opentable.com/terms", False),
        ("https://sevenrooms.com/login", False),
        ("https://covermanager.com/signup", False),
        ("https://calendly.com/careers", False),
        ("https://calendly.com.evil.example/demo", False),
        ("https://resy.com.evil.example/cities/demo", False),
        ("https://opentable.com.evil.example/r/demo", False),
    ],
)
def test_reservation_provider_findings_require_concrete_resources(
    href: str, expected: bool
) -> None:
    analysis = BeautifulSoupHtmlAnalyzer().analyze(
        f'<a href="{href}">Provider</a>', "https://site.example/"
    )
    kinds = {item.kind for item in analysis.findings}
    assert (FindingType.RESERVATION_PROVIDER_PRESENT in kinds) is expected
    assert (FindingType.HAS_RESERVATION_PATH in kinds) is expected


def test_local_booking_cta_still_derives_reservation_path() -> None:
    analysis = BeautifulSoupHtmlAnalyzer().analyze(
        '<a href="/reservar">Reservar cita</a>', "https://site.example/"
    )
    kinds = {item.kind for item in analysis.findings}
    assert FindingType.BOOKING_CTA_PRESENT in kinds
    assert FindingType.HAS_RESERVATION_PATH in kinds
    assert FindingType.RESERVATION_PROVIDER_PRESENT not in kinds


@pytest.mark.parametrize(
    ("href", "expected"),
    [
        ("https://maps.google.com/", False),
        ("https://maps.app.goo.gl/", False),
        ("https://www.google.com/maps", False),
        ("https://www.google.com/maps/", False),
        ("https://goo.gl/maps", False),
        ("https://goo.gl/maps/", False),
        ("https://www.openstreetmap.org/", False),
        ("https://maps.google.com/?utm_source=footer", False),
        ("https://www.google.com/maps?ref=footer", False),
        ("https://www.openstreetmap.org/#footer", False),
        ("https://www.google.com/maps/place/Clinica+Demo", True),
        ("https://www.google.com/maps/search/?api=1&query=Clinica+Demo", True),
        ("https://www.google.com/maps?query=Clinica+Demo", True),
        ("https://maps.google.com/?q=Clinica+Demo", True),
        ("https://maps.google.com/maps?q=Clinica+Demo", True),
        ("https://maps.app.goo.gl/AbCdEf123456", True),
        ("https://goo.gl/maps/AbCdEf123456", True),
        ("https://www.openstreetmap.org/node/123456", True),
        ("https://www.openstreetmap.org/way/123456", True),
        ("https://www.openstreetmap.org/relation/123456", True),
        ("https://www.openstreetmap.org/?mlat=-12.1&mlon=-77.0", True),
        ("https://maps.google.com/about", False),
        ("https://maps.google.com/help", False),
        ("https://www.openstreetmap.org/privacy", False),
        ("https://www.openstreetmap.org/login", False),
        ("https://www.openstreetmap.org/terms", False),
        ("https://maps.google.com.evil.example/place/demo", False),
        ("https://google.com.evil.example/maps/place/demo", False),
        ("https://openstreetmap.org.evil.example/node/123", False),
    ],
)
def test_map_links_require_concrete_location_resources(href: str, expected: bool) -> None:
    analysis = BeautifulSoupHtmlAnalyzer().analyze(
        f'<a href="{href}">Map</a>', "https://site.example/"
    )
    kinds = {item.kind for item in analysis.findings}
    assert (FindingType.MAP_LINK_PRESENT in kinds) is expected


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        ("<p>Horario: lunes 09:00 - 18:00</p>", True),
        ("<p>Horario de atención: Lun - Vie 09:00 - 18:00</p>", True),
        ("<p>Hours: Monday 9:00 AM - 6:00 PM</p>", True),
        ("<p>Business hours: Mon-Fri 09:00-18:00</p>", True),
        ("<p>Lunes a viernes: 09:00 - 18:00</p>", True),
        ("<p>Mon-Fri: 9:00 AM - 6:00 PM</p>", True),
        ("<p>La charla será el lunes de 09:00 - 11:00</p>", False),
        ("<p>Nuestro webinar del Monday será 10:00 AM - 11:00 AM</p>", False),
        ("<p>Evento lunes 18:00 - 20:00</p>", False),
        ("<p>Entrega programada para lunes 09:00 - 10:00</p>", False),
        ("<p>Meeting Monday 09:00 - 10:00</p>", False),
        ("<p>Workshop Mon-Fri 09:00 - 18:00</p>", False),
        ("<p>Lunes a viernes 09:00 - 18:00. Horario del webinar</p>", False),
        ("<p hidden>Horario: lunes 09:00 - 18:00</p>", False),
        ('<p aria-hidden="true">Hours: Monday 9:00 AM - 6:00 PM</p>', False),
        ("<script>Horario: lunes 09:00 - 18:00</script>", False),
        ("<template>Business hours: Mon-Fri 09:00-18:00</template>", False),
        (
            "<p>Evento martes 10:00 - 11:00</p><p>Horario de atención: lunes 09:00 - 18:00</p>",
            True,
        ),
    ],
)
def test_business_hours_require_schedule_context(html: str, expected: bool) -> None:
    analysis = BeautifulSoupHtmlAnalyzer().analyze(html, "https://site.example/")
    kinds = {item.kind for item in analysis.findings}
    assert (FindingType.BUSINESS_HOURS_PRESENT in kinds) is expected


@pytest.mark.parametrize(
    "html",
    [
        "<p>Horario de atención: lunes 09:00 - 18:00</p>",
        "<p>Business hours: Mon-Fri 09:00-18:00</p>",
        "<div><strong>Horario</strong><p>Lunes a viernes 09:00 - 18:00</p></div>",
        "<p>Lunes a viernes: 09:00 - 18:00</p>",
        "<p>Mon-Fri: 9:00 AM - 6:00 PM</p>",
        "<p>Reserva una cita con nuestros médicos.</p><section>"
        "<h2>Horario de atención</h2><p>Lunes a viernes 09:00 - 18:00</p></section>",
        "<section><h2>Horario de atención</h2><p>Lunes a viernes 09:00 - 18:00</p>"
        "<p>Agenda tu cita.</p></section>",
    ],
)
def test_business_hours_detects_operating_schedules_with_local_context(html: str) -> None:
    analysis = BeautifulSoupHtmlAnalyzer().analyze(html, "https://site.example/")
    assert FindingType.BUSINESS_HOURS_PRESENT in {item.kind for item in analysis.findings}


@pytest.mark.parametrize(
    "html",
    [
        "<div><h3>Dr. Juan Pérez</h3><p>Horario: lunes 09:00 - 12:00</p></div>",
        "<div><strong>Dra. María López</strong><p>Horario: martes 15:00 - 18:00</p></div>",
        "<div><h3>Cardiología - Dr. Pérez</h3><p>Horario miércoles 09:00 - 13:00</p></div>",
        "<p>Horario de consultas: lunes 09:00 - 12:00</p>",
        "<p>Horario de citas: martes 14:00 - 17:00</p>",
        "<div><h3>Citas disponibles</h3><p>Horario: lunes 09:00 - 11:00</p></div>",
        "<div><p>Agenda médica</p><p>Horario: viernes 14:00 - 17:00</p></div>",
        "<div><h3>Dr. Juan Pérez - Medicina General</h3><p>Horario: lunes 09:00 - 12:00</p></div>",
    ],
)
def test_business_hours_excludes_provider_and_appointment_schedules(html: str) -> None:
    analysis = BeautifulSoupHtmlAnalyzer().analyze(html, "https://site.example/")
    assert FindingType.BUSINESS_HOURS_PRESENT not in {item.kind for item in analysis.findings}


def test_business_hours_skips_provider_candidate_and_returns_later_operating_hours() -> None:
    html = (
        "<section><h3>Dr. Pérez</h3><p>Horario: lunes 09:00 - 12:00</p></section>"
        "<section><h2>Horario de atención</h2>"
        "<p>Lunes a viernes 08:00 - 18:00</p></section>"
    )
    analysis = BeautifulSoupHtmlAnalyzer().analyze(html, "https://site.example/")
    finding = next(
        item for item in analysis.findings if item.kind == FindingType.BUSINESS_HOURS_PRESENT
    )
    assert "08:00 - 18:00" in str(finding.value)


@pytest.mark.parametrize(
    ("url", "platform"),
    [
        ("https://facebook.com/", None),
        ("https://www.facebook.com/", None),
        ("https://instagram.com/", None),
        ("https://www.instagram.com/", None),
        ("https://linkedin.com/", None),
        ("https://www.linkedin.com/", None),
        ("https://facebook.com/?ref=footer", None),
        ("https://instagram.com/#footer", None),
        ("https://facebook.com.evil.example/demo", None),
        ("https://instagram.com.evil.example/demo", None),
        ("https://linkedin.com.evil.example/company/demo", None),
        ("https://facebook.com/clinica.demo", FindingType.FACEBOOK_LINK_PRESENT),
        ("https://www.facebook.com/clinica.demo/", FindingType.FACEBOOK_LINK_PRESENT),
        ("https://instagram.com/clinica.demo", FindingType.INSTAGRAM_LINK_PRESENT),
        ("https://www.instagram.com/clinica.demo/", FindingType.INSTAGRAM_LINK_PRESENT),
        ("https://linkedin.com/company/clinica-demo", FindingType.LINKEDIN_LINK_PRESENT),
        ("https://www.linkedin.com/company/clinica-demo/", FindingType.LINKEDIN_LINK_PRESENT),
    ],
)
def test_social_findings_require_concrete_resource_path(
    url: str, platform: FindingType | None
) -> None:
    analysis = BeautifulSoupHtmlAnalyzer().analyze(
        f'<a href="{url}">Social</a>', "https://site.example/"
    )
    social = {item.kind for item in analysis.findings} & {
        FindingType.FACEBOOK_LINK_PRESENT,
        FindingType.INSTAGRAM_LINK_PRESENT,
        FindingType.LINKEDIN_LINK_PRESENT,
        FindingType.SOCIAL_LINK_PRESENT,
    }
    assert social == ({platform, FindingType.SOCIAL_LINK_PRESENT} if platform else set())


def test_audit_persistence_provenance_freshness_force_and_http(engine: Engine) -> None:
    stored = company(engine, "http://site.example/")
    fetcher = fetcher_for(
        (FIXTURES / "booking.html").read_text(encoding="utf-8"), https_redirect=True
    )
    try:
        auditor = service(engine, fetcher)
        result = auditor.audit_company(stored.id)
        assert result.audit.status == AuditStatus.SUCCESS and result.reachable
        assert {
            FindingType.HTTPS_ENABLED,
            FindingType.REDIRECT_PRESENT,
            FindingType.WEBSITE_REACHABLE,
        } <= {item.kind for item in result.findings}
        repeated = auditor.audit_company(stored.id)
        assert (
            repeated.skipped_fresh
            and repeated.evidence_created == 0
            and repeated.audit.id == result.audit.id
        )
        forced = auditor.audit_company(stored.id, force=True)
        assert forced.audit.id != result.audit.id and forced.evidence_created > 0
        with SqlAlchemyUnitOfWork(engine) as uow:
            assert uow.repository.get(WebsiteAudit, result.audit.id) == result.audit
            source = uow.repository.get(Source, result.audit.source_id)
            assert (
                source is not None
                and source.url == stored.website
                and source.title == "Clínica Demo"
            )
            assert (
                source.metadata is not None
                and source.metadata["final_url"] == "https://site.example/"
            )
            evidence = uow.repository.list(Evidence, website_audit_id=result.audit.id)
            assert len(evidence) == result.evidence_created
            assert all(
                item.company_id == stored.id and item.source_id == source.id for item in evidence
            )
            assert (
                result.audit.finished_at is not None
                and result.audit.finished_at.utcoffset() == timedelta(0)
            )
    finally:
        fetcher.close()


def test_no_website_has_traceable_evidence_and_no_network(engine: Engine) -> None:
    stored = company(engine, None)
    fetcher = fetcher_for("")
    try:
        result = service(engine, fetcher).audit_company(stored.id)
        assert result.audit.status == AuditStatus.NO_WEBSITE and result.evidence_created == 1
        assert result.findings[0].kind == FindingType.NO_WEBSITE
        with SqlAlchemyUnitOfWork(engine) as uow:
            source = uow.repository.get(Source, result.audit.source_id)
            assert source is not None and source.url is None
            assert uow.repository.list(Evidence)[0].website_audit_id == result.audit.id
    finally:
        fetcher.close()


@pytest.mark.parametrize(
    "html,status,expected",
    [
        ("missing", 404, AuditStatus.FAILED),
        ("not HTML markup", 200, AuditStatus.PARTIAL),
        ("<title>Access denied</title>", 200, AuditStatus.FAILED),
        ('<form><input type="password"></form>', 200, AuditStatus.FAILED),
        ("<h1>Unclosed", 200, AuditStatus.SUCCESS),
    ],
)
def test_failed_partial_and_tolerant_html_audits(
    engine: Engine, html: str, status: int, expected: AuditStatus
) -> None:
    fetcher = fetcher_for(html, status=status)
    try:
        result = service(engine, fetcher).audit_company(company(engine).id)
        assert result.audit.status == expected
        if expected == AuditStatus.FAILED:
            assert FindingType.FETCH_ERROR in {item.kind for item in result.findings}
    finally:
        fetcher.close()


def test_http_https_missing_and_slow_threshold(engine: Engine) -> None:
    fetcher = fetcher_for("<h1>Home</h1>")
    try:
        result = service(engine, fetcher, AuditSettings(slow_response_ms=0.000001)).audit_company(
            company(engine, "http://site.example").id
        )
        assert {FindingType.HTTPS_MISSING, FindingType.SLOW_RESPONSE} <= {
            item.kind for item in result.findings
        }
    finally:
        fetcher.close()


def test_freshness_zero_disables_reuse(engine: Engine) -> None:
    fetcher = fetcher_for("<h1>Home</h1>")
    try:
        stored = company(engine)
        auditor = service(engine, fetcher, AuditSettings(freshness_days=0))
        first, second = auditor.audit_company(stored.id), auditor.audit_company(stored.id)
        assert first.audit.id != second.audit.id
    finally:
        fetcher.close()


def test_campaign_sequential_limit_no_website_skips_and_force(engine: Engine) -> None:
    with SqlAlchemyUnitOfWork(engine) as uow:
        writer = LeadService(uow)
        campaign = writer.create_campaign(
            Campaign(name="Health", campaign_type=CampaignType.HEALTH)
        )
        for index in range(3):
            stored = writer.upsert_company(
                Company(
                    canonical_name=f"Clinic {index}",
                    website=f"https://site{index}.example" if index < 2 else None,
                )
            )
            writer.create_lead(Lead(company_id=stored.id, campaign_id=campaign.id))
        uow.commit()
    fetcher = fetcher_for("<h1>Home</h1>")
    try:
        auditor = service(engine, fetcher)
        first = auditor.audit_campaign(campaign.id, limit=1)
        assert len(first.results) == 1 and first.skipped_no_website == 1
        second = auditor.audit_campaign(campaign.id, limit=2)
        assert sum(item.skipped_fresh for item in second.results) == 1
        assert sum(not item.skipped_fresh for item in second.results) == 1
        forced = auditor.audit_campaign(campaign.id, limit=2, force=True)
        assert len(forced.results) == 2 and all(not item.skipped_fresh for item in forced.results)
    finally:
        fetcher.close()


def test_audit_source_and_all_evidence_rollback_together(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    stored = company(engine)
    original = LeadService.add_evidence

    def fail_after_insert(self: LeadService, evidence: Evidence) -> Evidence:
        original(self, evidence)
        raise RuntimeError("Simulated evidence write failure")

    monkeypatch.setattr(LeadService, "add_evidence", fail_after_insert)
    fetcher = fetcher_for("<h1>Home</h1>")
    try:
        with pytest.raises(RuntimeError):
            service(engine, fetcher).audit_company(stored.id)
        with SqlAlchemyUnitOfWork(engine) as uow:
            assert uow.repository.list(WebsiteAudit) == []
            assert uow.repository.list(Source) == [] and uow.repository.list(Evidence) == []
    finally:
        fetcher.close()


def test_cli_company_domain_force_campaign_and_list(
    engine: Engine, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import lead_engine.cli.audit as cli

    stored = company(engine)
    # Inject mocked HTTP adapter at composition root; production CLI exposes no policy bypass.
    monkeypatch.setattr(cli, "HttpxFetcher", lambda settings: fetcher_for("<h1>Home</h1>"))
    runner = CliRunner()
    options = ["--database-url", database_url]
    result = runner.invoke(app, ["audit", "website", "--company-id", str(stored.id), *options])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["evidence_created"] > 0
    repeated = runner.invoke(app, ["audit", "website", "--domain", "site.example", *options])
    assert repeated.exit_code == 0 and json.loads(repeated.stdout)["skipped_fresh"]
    forced = runner.invoke(
        app, ["audit", "website", "--domain", "site.example", "--force", *options]
    )
    assert forced.exit_code == 0 and not json.loads(forced.stdout)["skipped_fresh"]
    assert runner.invoke(app, ["audit", "list", *options]).exit_code == 0
    assert runner.invoke(app, ["audit", "website", *options]).exit_code == 1
    with SqlAlchemyUnitOfWork(engine) as uow:
        writer = LeadService(uow)
        context = writer.create_campaign(Campaign(name="Health", campaign_type=CampaignType.HEALTH))
        writer.create_lead(Lead(company_id=stored.id, campaign_id=context.id))
        uow.commit()
    assert (
        runner.invoke(app, ["audit", "campaign", "--campaign", "Health", *options]).exit_code == 0
    )


def test_successful_audit_expires_after_seven_days(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    import lead_engine.application.auditor as module
    from lead_engine.domain.models import utc_now

    stored = company(engine)
    fetcher = fetcher_for("<h1>Home</h1>")
    try:
        auditor = service(engine, fetcher)
        first = auditor.audit_company(stored.id)
        future = utc_now() + timedelta(days=8)
        monkeypatch.setattr(module, "utc_now", lambda: future)
        second = auditor.audit_company(stored.id)
        assert not second.skipped_fresh and second.audit.id != first.audit.id
    finally:
        fetcher.close()


def test_migration_preserves_existing_evidence_and_score_associations(
    engine: Engine, database_url: str
) -> None:
    from decimal import Decimal
    from uuid import uuid4

    from alembic import command
    from sqlalchemy import text

    from lead_engine.domain.enums import SourceType
    from lead_engine.domain.models import LeadScore, ScoreComponent
    from lead_engine.infrastructure.schema import migration_config, upgrade_database

    command.downgrade(migration_config(database_url), "0002")
    evidence_id, score_id = uuid4(), uuid4()
    with SqlAlchemyUnitOfWork(engine) as uow:
        writer = LeadService(uow)
        stored = writer.upsert_company(Company(canonical_name="Existing"))
        source = writer.add_source(Source(source_type=SourceType.MANUAL))
        context = writer.create_campaign(
            Campaign(name="Existing", campaign_type=CampaignType.HEALTH)
        )
        lead = writer.create_lead(Lead(company_id=stored.id, campaign_id=context.id))
        # Old schema has no website_audit_id; insert its historical record explicitly.
        uow.repository.session.execute(
            text(
                "INSERT INTO evidence "
                "(id, company_id, source_id, evidence_type, statement, raw_value, "
                "confidence, observed_at, created_at) "
                "VALUES (:id, :company, :source, 'NOTE', 'Existing observation', "
                "NULL, 1, :time, :time)"
            ),
            {
                "id": evidence_id.hex,
                "company": stored.id.hex,
                "source": source.id.hex,
                "time": "2026-10-05 00:00:00.000000",
            },
        )
        score = LeadScore(
            id=score_id,
            lead_id=lead.id,
            total_score=Decimal(50),
            scoring_version="old",
            components=(
                ScoreComponent(
                    lead_score_id=score_id,
                    criterion="old",
                    points_awarded=Decimal(50),
                    max_points=Decimal(100),
                    explanation="Existing",
                    evidence_ids=(evidence_id,),
                ),
            ),
        )
        uow.repository.add(score)
        uow.commit()
    upgrade_database(database_url)
    with SqlAlchemyUnitOfWork(engine) as uow:
        evidence = uow.repository.get(Evidence, evidence_id)
        assert evidence is not None and evidence.website_audit_id is None
        assert uow.repository.get(LeadScore, score_id) == score
    command.downgrade(migration_config(database_url), "0002")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM evidence")) == 1
        assert connection.scalar(text("SELECT count(*) FROM component_evidence")) == 1


def test_truncated_html_audit_is_partial_positive_only_without_coverage(
    engine: Engine,
) -> None:
    prefix = (
        b"<html><head>"
        b'<meta name="viewport" content="width=device-width">'
        b"</head><body>"
        b'<a href="/book">Reservar cita</a>'
    )
    body = prefix + (b"x" * 2000)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(
            200,
            stream=httpx.ByteStream(body),
            headers={
                "Content-Type": "text/html; charset=utf-8",
                "Content-Length": str(len(body)),
            },
        )

    audit_settings = AuditSettings(
        min_request_interval_seconds=0,
        max_response_bytes=1024,
    )
    fetcher = HttpxFetcher(
        audit_settings,
        policy=PublicUrlPolicy(public_dns),
        transport=httpx.MockTransport(handler),
    )

    try:
        stored = company(engine)
        result = service(engine, fetcher, audit_settings).audit_company(stored.id)

        kinds = {item.kind for item in result.findings}

        assert result.audit.status == AuditStatus.PARTIAL
        assert result.reachable
        assert FindingType.WEBSITE_REACHABLE in kinds
        assert FindingType.MOBILE_VIEWPORT_PRESENT in kinds
        assert FindingType.BOOKING_CTA_PRESENT in kinds
        assert FindingType.HAS_RESERVATION_PATH in kinds

        # A partial document can establish only what was positively observed.
        assert FindingType.WEBSITE_SIGNAL_COVERAGE not in kinds
        assert FindingType.FETCH_ERROR not in kinds

        assert result.audit.error_code is None
        assert result.warnings
        assert any("trunc" in warning.lower() for warning in result.warnings)
    finally:
        fetcher.close()
