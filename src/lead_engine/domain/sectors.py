"""Small, stable economic-sector vocabulary independent of campaign policies."""

from collections.abc import Iterable, Mapping
from enum import StrEnum
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from lead_engine.domain.campaign_profiles import get_campaign_profile


class SectorSuggestionStatus(StrEnum):
    PROVISIONAL = "PROVISIONAL"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class EconomicSector(BaseModel):
    """An official broad business sector, optionally linked to a validated profile."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sector_id: str
    name: str
    description: str
    recommended_profile_id: str | None = None
    active: bool = True
    display_order: int

    @field_validator("sector_id")
    @classmethod
    def valid_sector_id(cls, value: str) -> str:
        if not value or value != value.strip() or value.lower() != value:
            raise ValueError("sector_id must be a non-empty lowercase identifier")
        return value

    @field_validator("name", "description")
    @classmethod
    def nonblank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("sector name and description must not be blank")
        return value

    @field_validator("display_order")
    @classmethod
    def positive_display_order(cls, value: int) -> int:
        if value < 1:
            raise ValueError("display_order must be positive")
        return value

    @field_validator("recommended_profile_id")
    @classmethod
    def existing_profile(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                get_campaign_profile(value)
            except LookupError as exc:
                raise ValueError(f"Unknown recommended campaign profile: {value}") from exc
        return value


class EconomicSubsector(BaseModel):
    """An optional commercial subcategory within one official sector."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    subsector_id: str
    sector_id: str
    name: str
    active: bool = True

    @field_validator("subsector_id", "sector_id")
    @classmethod
    def nonblank_id(cls, value: str) -> str:
        if not value or value != value.strip() or value.lower() != value:
            raise ValueError("IDs must be non-empty lowercase identifiers")
        return value

    @field_validator("name")
    @classmethod
    def nonblank_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("subsector name must not be blank")
        return value


class SectorSuggestion(BaseModel):
    """Human-entered, provisional mapping to the current sector catalog."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    requested_name: str
    suggested_sector_id: str
    suggested_subsector_id: str | None = None
    suggested_profile_id: str | None = None
    status: SectorSuggestionStatus = SectorSuggestionStatus.PROVISIONAL

    @field_validator("requested_name")
    @classmethod
    def nonblank_requested_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("requested_name must not be blank")
        return value

    @field_validator("suggested_sector_id")
    @classmethod
    def existing_suggested_sector(cls, value: str) -> str:
        try:
            get_sector(value)
        except LookupError as exc:
            raise ValueError(str(exc)) from exc
        return value

    @field_validator("suggested_profile_id")
    @classmethod
    def existing_suggested_profile(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                get_campaign_profile(value)
            except LookupError as exc:
                raise ValueError(f"Unknown suggested campaign profile: {value}") from exc
        return value

    @model_validator(mode="after")
    def subsector_belongs_to_suggested_sector(self) -> "SectorSuggestion":
        if self.suggested_subsector_id is not None:
            try:
                subsector = get_subsector(self.suggested_subsector_id)
            except LookupError as exc:
                raise ValueError(str(exc)) from exc
            if subsector.sector_id != self.suggested_sector_id:
                raise ValueError("Suggested subsector must belong to suggested sector")
        return self


SUGGEST_SECTOR = "SUGGEST_SECTOR"


def build_sector_registry(sectors: Iterable[EconomicSector]) -> Mapping[str, EconomicSector]:
    """Create a read-only catalog and reject duplicate IDs or display positions."""

    registry: dict[str, EconomicSector] = {}
    display_orders: set[int] = set()
    for sector in sectors:
        if sector.sector_id in registry:
            raise ValueError(f"Duplicate sector ID: {sector.sector_id}")
        if sector.display_order in display_orders:
            raise ValueError(f"Duplicate sector display order: {sector.display_order}")
        registry[sector.sector_id] = sector
        display_orders.add(sector.display_order)
    return MappingProxyType(dict(sorted(registry.items(), key=lambda item: item[1].display_order)))


def build_subsector_registry(
    subsectors: Iterable[EconomicSubsector],
    sectors: Mapping[str, EconomicSector],
) -> Mapping[str, EconomicSubsector]:
    """Create a read-only subsector catalog and reject duplicates and orphan rows."""

    registry: dict[str, EconomicSubsector] = {}
    for subsector in subsectors:
        if subsector.subsector_id in registry:
            raise ValueError(f"Duplicate subsector ID: {subsector.subsector_id}")
        if subsector.sector_id not in sectors:
            raise ValueError(
                f"Unknown sector for subsector {subsector.subsector_id}: {subsector.sector_id}"
            )
        registry[subsector.subsector_id] = subsector
    return MappingProxyType(registry)


DEFAULT_SECTORS = build_sector_registry(
    (
        EconomicSector(
            sector_id="health",
            name="Salud",
            description="Healthcare providers and related health services.",
            recommended_profile_id="health-v1",
            display_order=1,
        ),
        EconomicSector(
            sector_id="construction-real-estate",
            name="Construcción e inmobiliario",
            description="Construction, property development, and real estate services.",
            recommended_profile_id="construction-v1",
            display_order=2,
        ),
        EconomicSector(
            sector_id="industry-manufacturing",
            name="Industria y manufactura",
            description="Industrial production, processing, and manufacturing.",
            display_order=3,
        ),
        EconomicSector(
            sector_id="logistics-transport",
            name="Logística y transporte",
            description="Freight, logistics operations, warehousing, and transport.",
            display_order=4,
        ),
        EconomicSector(
            sector_id="commerce-distribution",
            name="Comercio y distribución",
            description="Retail, wholesale, and distribution businesses.",
            display_order=5,
        ),
        EconomicSector(
            sector_id="hospitality-tourism",
            name="Hotelería, gastronomía y turismo",
            description="Hospitality, food service, and tourism businesses.",
            recommended_profile_id="hospitality-v1",
            display_order=6,
        ),
        EconomicSector(
            sector_id="education",
            name="Educación",
            description="Schools, higher education, and training providers.",
            display_order=7,
        ),
        EconomicSector(
            sector_id="b2b-services",
            name="Servicios empresariales B2B",
            description="Business-to-business professional and operational services.",
            display_order=8,
        ),
        EconomicSector(
            sector_id="technology-digital",
            name="Tecnología y servicios digitales",
            description="Technology products and digital service providers.",
            display_order=9,
        ),
    )
)

DEFAULT_SUBSECTORS = build_subsector_registry(
    (
        EconomicSubsector(
            subsector_id="private-clinics", sector_id="health", name="Clínicas privadas"
        ),
        EconomicSubsector(subsector_id="diagnostics", sector_id="health", name="Diagnóstico"),
        EconomicSubsector(subsector_id="ophthalmology", sector_id="health", name="Oftalmología"),
        EconomicSubsector(subsector_id="dental", sector_id="health", name="Odontología"),
        EconomicSubsector(
            subsector_id="building-materials",
            sector_id="construction-real-estate",
            name="Materiales de construcción",
        ),
        EconomicSubsector(
            subsector_id="contractors", sector_id="construction-real-estate", name="Contratistas"
        ),
        EconomicSubsector(
            subsector_id="real-estate-development",
            sector_id="construction-real-estate",
            name="Desarrollo inmobiliario",
        ),
        EconomicSubsector(
            subsector_id="machinery-equipment",
            sector_id="construction-real-estate",
            name="Maquinaria y equipos",
        ),
        EconomicSubsector(
            subsector_id="food-processing",
            sector_id="industry-manufacturing",
            name="Procesamiento de alimentos",
        ),
        EconomicSubsector(
            subsector_id="agroindustry", sector_id="industry-manufacturing", name="Agroindustria"
        ),
        EconomicSubsector(
            subsector_id="plastics", sector_id="industry-manufacturing", name="Plásticos"
        ),
        EconomicSubsector(
            subsector_id="metalworking", sector_id="industry-manufacturing", name="Metalmecánica"
        ),
        EconomicSubsector(
            subsector_id="freight-transport",
            sector_id="logistics-transport",
            name="Transporte de carga",
        ),
        EconomicSubsector(
            subsector_id="logistics-operators",
            sector_id="logistics-transport",
            name="Operadores logísticos",
        ),
        EconomicSubsector(
            subsector_id="warehousing", sector_id="logistics-transport", name="Almacenamiento"
        ),
        EconomicSubsector(
            subsector_id="last-mile", sector_id="logistics-transport", name="Última milla"
        ),
        EconomicSubsector(subsector_id="hotels", sector_id="hospitality-tourism", name="Hoteles"),
        EconomicSubsector(
            subsector_id="restaurants", sector_id="hospitality-tourism", name="Restaurantes"
        ),
        EconomicSubsector(
            subsector_id="tourism-operators",
            sector_id="hospitality-tourism",
            name="Operadores turísticos",
        ),
        EconomicSubsector(subsector_id="schools", sector_id="education", name="Colegios"),
        EconomicSubsector(
            subsector_id="higher-education", sector_id="education", name="Educación superior"
        ),
        EconomicSubsector(subsector_id="training", sector_id="education", name="Capacitación"),
    ),
    DEFAULT_SECTORS,
)


def get_sector(sector_id: str) -> EconomicSector:
    """Get one official sector or fail clearly for an unknown ID."""

    try:
        return DEFAULT_SECTORS[sector_id]
    except KeyError as exc:
        raise LookupError(f"Unknown economic sector: {sector_id}") from exc


def get_subsector(subsector_id: str) -> EconomicSubsector:
    """Get one subsector or fail clearly for an unknown ID."""

    try:
        return DEFAULT_SUBSECTORS[subsector_id]
    except KeyError as exc:
        raise LookupError(f"Unknown economic subsector: {subsector_id}") from exc


def subsectors_for_sector(sector_id: str) -> tuple[EconomicSubsector, ...]:
    """Return a sector's subsectors in catalog insertion order."""

    get_sector(sector_id)
    return tuple(
        subsector for subsector in DEFAULT_SUBSECTORS.values() if subsector.sector_id == sector_id
    )


def create_sector_suggestion(
    requested_name: str,
    suggested_sector_id: str,
    suggested_subsector_id: str | None = None,
    suggested_profile_id: str | None = None,
) -> SectorSuggestion:
    """Create an explicitly mapped provisional suggestion without text classification."""

    return SectorSuggestion(
        requested_name=requested_name,
        suggested_sector_id=suggested_sector_id,
        suggested_subsector_id=suggested_subsector_id,
        suggested_profile_id=suggested_profile_id,
        status=SectorSuggestionStatus.PROVISIONAL,
    )
