"""Closed vocabulary: hypotheses and role angles, never free-text factual claims."""

from lead_engine.domain.enums import RoleCategory
from lead_engine.domain.outreach import (
    OutreachChannel,
    OutreachLanguage,
    OutreachPurpose,
)

# Statements require a fresh, positive structured observation with matching evidence.
OBSERVATIONS: dict[str, tuple[str, str]] = {
    "INSTAGRAM_ACTIVE": (
        "Las observaciones registran actividad en Instagram.",
        "Stored observations record activity on Instagram.",
    ),
    "FACEBOOK_ACTIVE": (
        "Las observaciones registran actividad en Facebook.",
        "Stored observations record activity on Facebook.",
    ),
    "MULTIPLE_LOCATIONS": (
        "Las observaciones indican varias ubicaciones.",
        "Stored observations indicate multiple locations.",
    ),
    "B2B_OPERATION": (
        "La evidencia registra una operación B2B.",
        "Evidence records a B2B operation.",
    ),
    "PRIVATE_EVENTS": (
        "La evidencia registra una oferta de eventos privados.",
        "Evidence records a private events offering.",
    ),
    "SERVICES_PAGE_PRESENT": (
        "La auditoría encontró una página de servicios.",
        "The audit found a services page.",
    ),
    "CATALOG_PRESENT": ("La auditoría encontró un catálogo.", "The audit found a catalog."),
}

ROLE_ANGLES: dict[RoleCategory, tuple[str, str]] = {
    RoleCategory.OWNER: (
        "el crecimiento y el proceso comercial",
        "growth and the commercial process",
    ),
    RoleCategory.FOUNDER: (
        "el crecimiento y el proceso comercial",
        "growth and the commercial process",
    ),
    RoleCategory.GENERAL_MANAGEMENT: (
        "la eficacia comercial y el recorrido del cliente",
        "commercial effectiveness and the customer journey",
    ),
    RoleCategory.MARKETING: (
        "la continuidad de campañas y la experiencia de marca",
        "campaign continuity and the brand experience",
    ),
    RoleCategory.COMMERCIAL: (
        "la captación, cotización y seguimiento comercial",
        "lead capture, quotations and sales follow-up",
    ),
    RoleCategory.DIGITAL: (
        "la conversión digital y su integración",
        "digital conversion and integration",
    ),
    RoleCategory.CUSTOMER_EXPERIENCE: (
        "el recorrido completo del cliente",
        "the end-to-end customer journey",
    ),
    RoleCategory.MEDICAL_DIRECTOR: (
        "la información al paciente y la confianza",
        "patient information and trust",
    ),
    RoleCategory.ADMINISTRATION: (
        "el flujo operativo y los traspasos entre equipos",
        "workflow and handoffs between teams",
    ),
    RoleCategory.UNKNOWN: ("el recorrido del cliente", "the customer journey"),
}

CAMPAIGN_ANGLES: dict[str, tuple[str, str]] = {
    "HEALTH": ("facilitar evaluaciones y citas", "make evaluations and appointments easier"),
    "CONSTRUCTION": (
        "facilitar consultas de productos y cotizaciones",
        "make product enquiries and quotations easier",
    ),
    "HOSPITALITY": (
        "facilitar reservas y consultas de eventos",
        "make reservations and event enquiries easier",
    ),
}


OPPORTUNITY_ANGLES: dict[str, tuple[str, str]] = {
    "APPOINTMENT_CONVERSION": (
        "facilitar evaluaciones y citas",
        "make evaluations and appointments easier",
    ),
    "QUOTE_CONVERSION": (
        "facilitar consultas y cotizaciones",
        "make enquiries and quotations easier",
    ),
    "RESERVATION_CONVERSION": ("facilitar reservas", "make reservations easier"),
    "PRODUCT_DISCOVERY": ("facilitar la búsqueda de productos", "make product discovery easier"),
    "SERVICE_DISCOVERY": ("facilitar la búsqueda de servicios", "make service discovery easier"),
    "CATALOG_IMPROVEMENT": ("clarificar el recorrido del catálogo", "clarify the catalog journey"),
    "B2B_LEAD_CAPTURE": ("facilitar consultas B2B", "make B2B enquiries easier"),
    "PRIVATE_EVENTS": (
        "facilitar consultas de eventos privados",
        "make private event enquiries easier",
    ),
    "CUSTOMER_JOURNEY": ("clarificar el recorrido del cliente", "clarify the customer journey"),
    "DIGITAL_TRUST": ("mejorar la claridad de la información", "improve information clarity"),
    "CONTACTABILITY": ("facilitar el contacto", "make contact easier"),
    "ECOMMERCE": ("simplificar el recorrido de compra", "simplify the purchase journey"),
    "BRAND_EXPERIENCE": ("conectar marca y recorrido digital", "connect brand and digital journey"),
    "LOCAL_DISCOVERY": ("facilitar la búsqueda local", "make local discovery easier"),
    "MULTI_LOCATION_EXPERIENCE": (
        "clarificar el recorrido entre sedes",
        "clarify the journey across locations",
    ),
}


class V1OutreachPolicy:
    version = "outreach-v1"

    def render(
        self,
        *,
        campaign: str,
        opportunity: str,
        role: RoleCategory,
        language: OutreachLanguage,
        channel: OutreachChannel,
        purpose: OutreachPurpose,
        connection: bool,
        company: str,
        name: str,
        observation: str,
        referral: str,
        authority: bool,
    ) -> tuple[str | None, str]:
        en = language == OutreachLanguage.EN
        index = int(en)
        angle = OPPORTUNITY_ANGLES.get(opportunity, CAMPAIGN_ANGLES[campaign])[index]
        role_angle = ROLE_ANGLES[role][index]
        greeting = f"Hello {name}." if en else f"Hola {name}."
        if channel == OutreachChannel.WEBSITE_CONTACT_FORM:
            greeting = "Hello team." if en else "Hola, equipo."
        if connection:
            context = (
                f"Let's connect to explore how to {angle}, focusing on {role_angle}."
                if en
                else f"Me gustaría conectar para {angle}, con foco en {role_angle}."
            )
            cta = "Do you handle this area?" if en else "¿Ves esta área?"
            # Identity is stored separately: long names/company names need not be repeated.
            body = " ".join(x for x in ("Hello." if en else "Hola.", referral, context, cta) if x)
            return None, body
        context = (
            f"For {company}, could we explore how to {angle}, focusing on {role_angle}?"
            if en
            else f"Para {company}, ¿podríamos explorar cómo {angle}, con foco en {role_angle}?"
        )
        if channel == OutreachChannel.WHATSAPP:
            context = (
                f"Could we explore how to {angle}, with a focus on {role_angle}?"
                if en
                else f"Quería consultarte cómo {angle}, con foco en {role_angle}."
            )
        if purpose == OutreachPurpose.FOLLOW_UP:
            greeting += (
                " I'm following up on my recorded previous contact."
                if en
                else " Retomo mi contacto anterior registrado."
            )
        elif purpose == OutreachPurpose.POST_CONNECTION_MESSAGE:
            greeting += (
                " Thanks for accepting the invitation."
                if en
                else " Gracias por aceptar la invitación."
            )
        if not authority:
            cta = (
                "Do you handle this directly, or does another team member?"
                if en
                else "¿Lo ves tú directamente o lo maneja otra persona del equipo?"
            )
        elif purpose == OutreachPurpose.MEETING_REQUEST:
            cta = (
                "Could we talk for 15 minutes this week?"
                if en
                else "¿Podemos conversar 15 minutos esta semana?"
            )
        else:
            cta = (
                "If useful, I can share the analysis."
                if en
                else "Si te parece útil, te comparto el análisis."
            )
        if channel == OutreachChannel.WEBSITE_CONTACT_FORM:
            cta = (
                "Who on the team handles this area?"
                if en
                else "¿Qué persona del equipo gestiona esta área?"
            )
        if channel == OutreachChannel.PHONE_SCRIPT:
            return None, "\n".join(
                (
                    "Talking points:" if en else "Puntos para conversar:",
                    f"- {referral}" if referral else "- " + greeting,
                    "- " + observation if observation else "- " + context,
                    "- " + context if observation else "- " + cta,
                    "- " + cta if observation else "",
                )
            ).rstrip()
        body = "\n\n".join(x for x in (greeting, referral, observation, context, cta) if x)
        subject = (
            (
                {
                    "HEALTH": ("Evaluaciones y citas", "Evaluations and appointments"),
                    "CONSTRUCTION": ("Consultas y cotizaciones", "Enquiries and quotations"),
                    "HOSPITALITY": (
                        "Reservas y experiencia de marca",
                        "Reservations and brand experience",
                    ),
                }[campaign][index]
            )
            if channel == OutreachChannel.EMAIL
            else None
        )
        return subject, body
