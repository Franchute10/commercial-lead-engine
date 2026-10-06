"""Contact research transactions; providers run outside write transactions."""

from datetime import datetime, timedelta
from uuid import UUID

from lead_engine.application.discovery import UnitOfWorkFactory
from lead_engine.domain.contact_research import (
    ContactCandidate,
    ContactDiscoveryProvider,
    ContactIdentity,
    ContactRecommendation,
    DecisionMakerResearchRun,
    PublicChannel,
    RecommendationResult,
    VerificationState,
    identity_keys,
    is_supported,
    role_points,
    source_reliability,
)
from lead_engine.domain.enums import RoleCategory
from lead_engine.domain.errors import IdentityConflictError, NotFoundError
from lead_engine.domain.identity import normalize_name
from lead_engine.domain.models import (
    Campaign,
    Company,
    Contact,
    Evidence,
    Lead,
    LeadScore,
    Source,
    utc_now,
)


def _observed_title(evidence: Evidence) -> str:
    if isinstance(evidence.raw_value, dict):
        snapshot = evidence.raw_value.get("candidate")
        if isinstance(snapshot, dict):
            return normalize_name(str(snapshot.get("role_title") or ""))
    return ""


class DecisionMakerResearchService:
    def __init__(self, uow_factory: UnitOfWorkFactory, freshness_days: int = 30) -> None:
        if not 0 <= freshness_days <= 365:
            raise ValueError("Freshness must be 0–365 days")
        self.factory = uow_factory
        self.freshness = timedelta(days=freshness_days)

    def research(
        self,
        company_id: UUID,
        provider: ContactDiscoveryProvider,
        lead_id: UUID | None = None,
        force: bool = False,
    ) -> DecisionMakerResearchRun:
        started = utc_now()
        with self.factory() as uow:
            company = uow.repository.get(Company, company_id)
            if company is None:
                raise NotFoundError("Company not found")
            if lead_id is not None:
                lead = uow.repository.get(Lead, lead_id)
                if lead is None or lead.company_id != company_id:
                    raise ValueError("Lead must belong to company")
            runs = uow.repository.list(
                DecisionMakerResearchRun, company_id=company_id, provider=provider.name
            )
            fresh = [
                r
                for r in runs
                if r.status == "SUCCESS" and started - r.finished_at <= self.freshness
            ]
            if fresh and not force:
                return max(fresh, key=lambda r: r.finished_at)
        try:
            candidates = provider.discover(company)
        except (OSError, ValueError):
            failed = DecisionMakerResearchRun(
                lead_id=lead_id,
                company_id=company.id,
                provider=provider.name,
                started_at=started,
                finished_at=utc_now(),
                candidates_found=0,
                contacts_created=0,
                contacts_reused=0,
                conflicts=0,
                status="FAILED",
                warnings=["Provider failed; no contact claims persisted"],
            )
            with self.factory() as uow:
                uow.repository.add(failed)
                uow.commit()
            return failed
        created = reused = conflicts = 0
        with self.factory() as uow:
            repo = uow.repository
            for candidate in candidates:
                candidate = ContactCandidate.model_validate(candidate.model_dump())
                if candidate.company_id not in {None, company.id}:
                    raise IdentityConflictError("Candidate belongs to another company")
                if candidate.company_name and normalize_name(
                    candidate.company_name
                ) != normalize_name(company.canonical_name):
                    raise IdentityConflictError("Candidate names another company")
                keys = identity_keys(candidate, company.id)
                matches = {
                    i.contact_id for key in keys for i in repo.list(ContactIdentity, key=key)
                }
                # Legacy contacts are eligible only under exact compatible identity.
                matches.update(
                    c.id
                    for c in repo.list(Contact, company_id=company.id)
                    if normalize_name(c.full_name) == normalize_name(candidate.full_name)
                    and c.role_category == candidate.role_category
                )
                if len(matches) > 1:
                    raise IdentityConflictError("Conflicting identity keys; review manually")
                contact = repo.get(Contact, next(iter(matches))) if matches else None
                if contact:
                    if normalize_name(contact.full_name) != normalize_name(candidate.full_name):
                        raise IdentityConflictError("Shared profile/email has conflicting names")
                    known = repo.list(ContactIdentity, contact_id=contact.id)
                    for kind in (":profile:", ":email:"):
                        previous = {i.key for i in known if kind in i.key}
                        incoming = {key for key in keys if kind in key}
                        if previous and incoming and previous.isdisjoint(incoming):
                            raise IdentityConflictError(
                                "Conflicting public identity; review manually"
                            )
                    reused += 1
                    history = repo.list(
                        Evidence, company_id=company.id, evidence_type="CONTACT_ROLE"
                    )
                    if any(
                        isinstance(e.raw_value, dict)
                        and e.raw_value.get("contact_id") == str(contact.id)
                        and (
                            e.raw_value.get("role_category") != candidate.role_category.value
                            or _observed_title(e) != normalize_name(candidate.role_title or "")
                        )
                        for e in history
                    ):
                        conflicts += 1
                else:
                    contact = Contact(
                        company_id=company.id,
                        full_name=candidate.full_name,
                        role_title=candidate.role_title,
                        role_category=candidate.role_category,
                        linkedin_url=candidate.linkedin_url,
                        email=candidate.public_email,
                        phone=candidate.public_phone,
                        confidence=candidate.confidence,
                    )
                    repo.add(contact)
                    created += 1
                for key in keys:
                    if not repo.list(ContactIdentity, key=key):
                        repo.add(ContactIdentity(key=key, contact_id=contact.id))
                source = Source(
                    source_type=candidate.source_type,
                    url=candidate.source_url,
                    title=candidate.source_title,
                    retrieved_at=utc_now(),
                    metadata=candidate.metadata,
                )
                repo.add(source)
                claims = [
                    ("CONTACT_ROLE", candidate.role_title or "Unknown role"),
                    (
                        "CONTACT_COMPANY_ASSOCIATION",
                        candidate.association_statement or "Unverified candidate association",
                    ),
                ]
                for kind, value in (
                    (
                        "CONTACT_PUBLIC_PROFILE",
                        candidate.linkedin_url or candidate.public_profile_url,
                    ),
                    ("CONTACT_PUBLIC_EMAIL", candidate.public_email),
                    ("CONTACT_PUBLIC_PHONE", candidate.public_phone),
                ):
                    if value:
                        claims.append((kind, value))
                if candidate.context_statement:
                    claims.append(("CONTACT_COMPANY_CONTEXT", candidate.context_statement))
                for kind, statement in claims:
                    repo.add(
                        Evidence(
                            company_id=company.id,
                            source_id=source.id,
                            evidence_type=kind,
                            statement=statement,
                            observed_at=candidate.observed_at,
                            confidence=candidate.confidence,
                            raw_value={
                                "contact_id": str(contact.id),
                                "candidate": candidate.model_dump(mode="json"),
                                "role_category": candidate.role_category.value,
                                "verification": "SUPPORTED"
                                if is_supported(candidate, company)
                                else "UNVERIFIED",
                            },
                        )
                    )
            run = DecisionMakerResearchRun(
                lead_id=lead_id,
                company_id=company.id,
                provider=provider.name,
                started_at=started,
                finished_at=utc_now(),
                candidates_found=len(candidates),
                contacts_created=created,
                contacts_reused=reused,
                conflicts=conflicts,
                status="PARTIAL" if provider.warnings else "SUCCESS",
                warnings=provider.warnings,
            )
            repo.add(run)
            uow.commit()
        return run

    def recommend_contacts(
        self, lead_id: UUID, limit: int = 3, as_of: datetime | None = None
    ) -> RecommendationResult:
        if not 1 <= limit <= 100:
            raise ValueError("Limit must be 1–100")
        now = as_of or utc_now()
        with self.factory() as uow:
            repo = uow.repository
            lead = repo.get(Lead, lead_id)
            if lead is None:
                raise NotFoundError("Lead not found")
            company = repo.get(Company, lead.company_id)
            campaign = repo.get(Campaign, lead.campaign_id)
            if company is None or campaign is None:
                raise NotFoundError("Lead relationships missing")
            results = []
            evidence = repo.list(Evidence, company_id=company.id)
            for contact in repo.list(Contact, company_id=company.id):
                observations = []
                for e in evidence:
                    if (
                        e.evidence_type == "CONTACT_ROLE"
                        and isinstance(e.raw_value, dict)
                        and e.raw_value.get("contact_id") == str(contact.id)
                    ):
                        c = ContactCandidate.model_validate(e.raw_value["candidate"])
                        if e.created_at <= now and is_supported(c, company, now):
                            observations.append((e, c))
                if not observations:
                    continue
                observations.sort(
                    key=lambda pair: (
                        pair[0].observed_at,
                        source_reliability(pair[1], company),
                        str(pair[0].id),
                    ),
                    reverse=True,
                )
                current, candidate = observations[0]
                stale = now - current.observed_at > self.freshness
                tied = [
                    (c.role_category, normalize_name(c.role_title or ""))
                    for e, c in observations
                    if e.observed_at == current.observed_at
                ]
                conflicted = len(set(tied)) > 1
                role = role_points(
                    candidate.role_category,
                    campaign.campaign_type,
                    candidate.context,
                    candidate.role_title or "",
                )
                channels = []
                refs = []
                for e in evidence:
                    if not isinstance(e.raw_value, dict) or e.raw_value.get("contact_id") != str(
                        contact.id
                    ):
                        continue
                    if e.source_id != current.source_id:
                        continue
                    refs.append(e.id)
                    if e.evidence_type in {
                        "CONTACT_PUBLIC_PROFILE",
                        "CONTACT_PUBLIC_EMAIL",
                        "CONTACT_PUBLIC_PHONE",
                    }:
                        kind = {
                            "CONTACT_PUBLIC_PROFILE": "LINKEDIN"
                            if candidate.linkedin_url
                            else "OTHER",
                            "CONTACT_PUBLIC_EMAIL": "EMAIL",
                            "CONTACT_PUBLIC_PHONE": "PHONE",
                        }[e.evidence_type]
                        channels.append(
                            PublicChannel(channel_type=kind, value=e.statement, evidence_id=e.id)
                        )
                reliability = source_reliability(candidate, company)
                fit = role + 20 + (0 if stale else 10) + reliability + (10 if channels else 0)
                warnings = []
                if stale:
                    warnings.append("Role evidence is stale; reconfirm before outreach")
                if (
                    len(
                        {
                            (c.role_category, normalize_name(c.role_title or ""))
                            for _, c in observations
                        }
                    )
                    > 1
                ):
                    warnings.append("Role history differs; latest supported observation used")
                if conflicted:
                    warnings.append("Same-date roles conflict; manual review required")
                    fit = max(0, fit - 20)
                results.append(
                    ContactRecommendation(
                        contact_id=contact.id,
                        full_name=contact.full_name,
                        current_role=candidate.role_title or "Unknown",
                        role_category=candidate.role_category,
                        fit_score=fit,
                        confidence="HIGH"
                        if reliability >= 8
                        and candidate.confidence >= 0.85
                        and not stale
                        and not conflicted
                        else "MEDIUM",
                        verification=VerificationState.CONFLICTED
                        if conflicted
                        else VerificationState.STALE
                        if stale
                        else VerificationState.SUPPORTED,
                        channels=channels,
                        evidence_ids=refs,
                        explanation=[
                            f"Campaign role relevance: {role}/50",
                            "Attributable person-role-company evidence: 20/20",
                            f"Recency: {0 if stale else 10}/10",
                            f"Source reliability: {reliability}/10",
                            f"Published channel: {10 if channels else 0}/10",
                        ],
                        warnings=warnings,
                    )
                )
            results.sort(
                key=lambda r: (-r.fit_score, normalize_name(r.full_name), str(r.contact_id))
            )
            return RecommendationResult(
                lead_id=lead_id,
                contacts=results[:limit],
                target_roles=[]
                if results
                else [
                    RoleCategory.MARKETING,
                    RoleCategory.GENERAL_MANAGEMENT,
                    RoleCategory.DIGITAL,
                ],
                message="Supported contacts; human review required"
                if results
                else "No supported individual contact found",
            )

    def research_campaign(
        self,
        campaign_id: UUID,
        provider: ContactDiscoveryProvider,
        min_lead_score: int | None = None,
        limit: int = 5,
        force: bool = False,
    ) -> list[DecisionMakerResearchRun]:
        if not 1 <= limit <= 100:
            raise ValueError("Limit must be 1–100")
        if min_lead_score is not None and not 0 <= min_lead_score <= 100:
            raise ValueError("Score must be 0–100")
        with self.factory() as uow:
            if uow.repository.get(Campaign, campaign_id) is None:
                raise NotFoundError("Campaign not found")
            leads = uow.repository.list(Lead, campaign_id=campaign_id)
            qualified = []
            for lead in leads:
                scores = uow.repository.list(LeadScore, lead_id=lead.id)
                latest = max(scores, key=lambda s: s.calculated_at) if scores else None
                if min_lead_score is None or (latest and latest.total_score >= min_lead_score):
                    qualified.append(lead)
        return [
            self.research(lead.company_id, provider, lead.id, force) for lead in qualified[:limit]
        ]
