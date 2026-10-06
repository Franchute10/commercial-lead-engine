"""Offline behavioral coverage: human gates, evidence and persistence boundaries."""

import json
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine, delete
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from examples.outreach_fixture_demo import create_outreach_fixtures, run_acceptance
from lead_engine.application.outreach import OutreachDraftService
from lead_engine.application.outreach_export import OutreachFormat, export_drafts
from lead_engine.application.research import CommercialResearchService
from lead_engine.application.scoring import CommercialScoringService
from lead_engine.application.shortlist import DailyShortlistService
from lead_engine.cli.main import app
from lead_engine.domain.enums import InteractionType, LeadStatus, RoleCategory, SourceType
from lead_engine.domain.errors import DuplicateError, NotFoundError
from lead_engine.domain.models import Evidence, Lead, LeadInteraction, Source, utc_now
from lead_engine.domain.outreach import (
    DraftStatus,
    GroundedClaim,
    OutreachChannel,
    OutreachDraft,
    OutreachEvent,
    OutreachLanguage,
    OutreachPurpose,
    OutreachSettings,
)
from lead_engine.domain.outreach_policy import OBSERVATIONS, ROLE_ANGLES, V1OutreachPolicy
from lead_engine.domain.shortlist import NextAction
from lead_engine.infrastructure.orm import EvidenceRow, LeadRow
from lead_engine.infrastructure.repositories import SqlAlchemyUnitOfWork


@pytest.fixture
def ids(engine: Engine) -> tuple[UUID, ...]:
    return create_outreach_fixtures(engine)


@pytest.fixture
def writer(engine: Engine) -> OutreachDraftService:
    return OutreachDraftService(lambda: SqlAlchemyUnitOfWork(engine))


def activity(
    engine: Engine,
    lead_id: UUID,
    contact_id: UUID | None,
    kind: InteractionType,
    days: int = 8,
    outcome: str | None = None,
) -> LeadInteraction:
    record = LeadInteraction(
        lead_id=lead_id,
        contact_id=contact_id,
        interaction_type=kind,
        occurred_at=utc_now() - timedelta(days=days),
        outcome=outcome,
    )
    with SqlAlchemyUnitOfWork(engine) as uow:
        uow.repository.add(record)
        uow.commit()
    return record


def extra_channel(
    engine: Engine, lead_id: UUID, contact_id: UUID, kind: str, business: bool = True
) -> UUID:
    with SqlAlchemyUnitOfWork(engine) as uow:
        lead = uow.repository.get(Lead, lead_id)
        assert lead is not None
        source = Source(source_type=SourceType.WEBSITE, url="https://fixture.example/contact")
        uow.repository.add(source)
        e = Evidence(
            company_id=lead.company_id,
            source_id=source.id,
            evidence_type=kind,
            statement="https://wa.me/51999000000"
            if "WHATSAPP" in kind
            else "https://fixture.example/contact",
            raw_value={"contact_id": str(contact_id), "business_facing": business},
            confidence=0.9,
            observed_at=utc_now(),
        )
        uow.repository.add(e)
        uow.commit()
        return e.id


def test_manual_acceptance(engine: Engine) -> None:
    views = run_acceptance(engine)
    assert len(views) == 4 and all(v.draft.outreach_version == "outreach-v1" for v in views)


@pytest.mark.parametrize(
    "index,word", [(0, "citas"), (1, "cotizaciones"), (2, "reservas"), (3, "crecimiento")]
)
def test_campaign_role_grounding(
    writer: OutreachDraftService, ids: tuple[UUID, ...], engine: Engine, index: int, word: str
) -> None:
    view = writer.draft(ids[index], OutreachChannel.EMAIL)
    assert word in view.draft.body and view.draft.subject
    assert view.status == DraftStatus.DRAFT
    with SqlAlchemyUnitOfWork(engine) as uow:
        for claim in view.draft.claims:
            assert claim.text in view.draft.body
            for eid in claim.evidence_ids:
                e = uow.repository.get(Evidence, eid)
                assert e is not None and uow.repository.get(Source, e.source_id) is not None
    assert writer.show(view.draft.id) == view


@pytest.mark.parametrize("role", list(RoleCategory))
@pytest.mark.parametrize("language", list(OutreachLanguage))
def test_role_adaptation(role: RoleCategory, language: OutreachLanguage) -> None:
    _, body = V1OutreachPolicy().render(
        campaign="HEALTH",
        opportunity="APPOINTMENT_CONVERSION",
        role=role,
        language=language,
        channel=OutreachChannel.EMAIL,
        purpose=OutreachPurpose.FIRST_CONTACT,
        connection=False,
        company="Fixture",
        name="Person",
        observation="",
        referral="",
        authority=False,
    )
    assert ROLE_ANGLES[role][int(language == OutreachLanguage.EN)] in body
    assert "otra persona" in body or "another team" in body
    if role == RoleCategory.MEDICAL_DIRECTOR:
        assert (
            "marketing" not in body and "patient" in body
            if language == OutreachLanguage.EN
            else "paciente" in body
        )


@pytest.mark.parametrize(
    "channel", [OutreachChannel.LINKEDIN, OutreachChannel.EMAIL, OutreachChannel.PHONE_SCRIPT]
)
def test_standard_channels(
    writer: OutreachDraftService, ids: tuple[UUID, ...], channel: OutreachChannel
) -> None:
    draft = writer.draft(ids[0], channel).draft
    assert len(draft.body) <= draft.settings.body_limit(channel, draft.connection)
    if channel == OutreachChannel.LINKEDIN:
        assert draft.connection and len(draft.body) <= 300
    if channel == OutreachChannel.PHONE_SCRIPT:
        assert draft.body.startswith("Puntos para conversar:") and "- " in draft.body


@pytest.mark.parametrize(
    "channel,kind",
    [
        (OutreachChannel.WHATSAPP, "CONTACT_PUBLIC_WHATSAPP"),
        (OutreachChannel.WEBSITE_CONTACT_FORM, "CONTACT_PUBLIC_CONTACT_FORM"),
    ],
)
def test_extra_public_channels(
    engine: Engine,
    writer: OutreachDraftService,
    ids: tuple[UUID, ...],
    channel: OutreachChannel,
    kind: str,
) -> None:
    contact_id = writer.draft(ids[0], OutreachChannel.EMAIL).draft.contact_id
    eid = extra_channel(engine, ids[0], contact_id, kind)
    draft = writer.draft(ids[0], channel).draft
    assert eid in draft.supporting_evidence_ids
    if channel == OutreachChannel.WEBSITE_CONTACT_FORM:
        assert draft.body.startswith("Hola, equipo.")
        assert draft.shortlist_action == NextAction.PREPARE_CONTACT_FORM


def test_whatsapp_never_inferred(
    engine: Engine, writer: OutreachDraftService, ids: tuple[UUID, ...]
) -> None:
    contact_id = writer.draft(ids[0], OutreachChannel.EMAIL).draft.contact_id
    with pytest.raises(ValueError, match="no outreach"):
        writer.draft(ids[0], OutreachChannel.WHATSAPP)
    extra_channel(engine, ids[0], contact_id, "CONTACT_PUBLIC_WHATSAPP", False)
    with pytest.raises(ValueError, match="no outreach"):
        writer.draft(ids[0], OutreachChannel.WHATSAPP)


def test_referral_cold_and_force_history(
    writer: OutreachDraftService, ids: tuple[UUID, ...]
) -> None:
    cold = writer.draft(ids[0], OutreachChannel.EMAIL)
    warm = writer.draft(ids[3], OutreachChannel.EMAIL)
    assert "Carlos Pérez" not in cold.draft.body and "Carlos Pérez" in warm.draft.body
    assert warm.draft.purpose == OutreachPurpose.REFERRAL_INTRO
    with pytest.raises(DuplicateError):
        writer.draft(ids[3], OutreachChannel.EMAIL)
    new = writer.draft(ids[3], OutreachChannel.EMAIL, force=True)
    assert new.draft.id != warm.draft.id and new.draft.body == warm.draft.body
    assert writer.show(warm.draft.id).status == DraftStatus.SUPERSEDED
    assert len(writer.list_drafts(lead_id=ids[3])) == 2
    with pytest.raises(ValueError, match="referral"):
        writer.draft(ids[0], OutreachChannel.EMAIL, OutreachPurpose.REFERRAL_INTRO)


@pytest.mark.parametrize("mutation", ["malformed", "stale", "wrong_contact", "not_recommended"])
def test_invalid_referral_ignored(
    engine: Engine, writer: OutreachDraftService, ids: tuple[UUID, ...], mutation: str
) -> None:
    with Session(engine) as session:
        row = session.query(EvidenceRow).filter(EvidenceRow.evidence_type == "REFERRAL_PATH").one()
        if mutation == "malformed":
            row.raw_value = "Referred by Carlos"
        elif mutation == "stale":
            row.observed_at = utc_now() - timedelta(days=90)
        else:
            assert isinstance(row.raw_value, dict)
            row.raw_value = {
                **row.raw_value,
                **(
                    {"contact_id": str(uuid4())}
                    if mutation == "wrong_contact"
                    else {"recommended_contact": False}
                ),
            }
        session.commit()
    draft = writer.draft(ids[3], OutreachChannel.EMAIL).draft
    assert draft.referral_evidence_id is None and "Carlos" not in draft.body


@pytest.mark.parametrize(
    "status",
    [
        LeadStatus.RESPONDED,
        LeadStatus.WON,
        LeadStatus.LOST,
        LeadStatus.ARCHIVED,
        LeadStatus.MEETING,
    ],
)
def test_pipeline_no_action(
    engine: Engine, writer: OutreachDraftService, ids: tuple[UUID, ...], status: LeadStatus
) -> None:
    with Session(engine) as session:
        row = session.get(LeadRow, ids[0])
        assert row is not None
        row.status = status
        session.commit()
    with pytest.raises(ValueError, match="no outreach"):
        writer.draft(ids[0], force=True)


def test_cooldown_force_and_suppression(
    engine: Engine, writer: OutreachDraftService, ids: tuple[UUID, ...]
) -> None:
    activity(engine, ids[0], None, InteractionType.EMAIL, days=0)
    with pytest.raises(ValueError, match="NO_ACTION"):
        writer.draft(ids[0], force=True)
    DailyShortlistService(lambda: SqlAlchemyUnitOfWork(engine)).suppress(ids[1], 5, "Human pause")
    with pytest.raises(ValueError, match="NO_ACTION"):
        writer.draft(ids[1], force=True)


def test_research_first_no_contact_and_roles_only(
    engine: Engine, writer: OutreachDraftService, ids: tuple[UUID, ...]
) -> None:
    with pytest.raises(ValueError):
        writer.draft(ids[4])
    with Session(engine) as session:
        session.execute(delete(EvidenceRow).where(EvidenceRow.evidence_type == "CONTACT_ROLE"))
        session.commit()
    with pytest.raises(ValueError, match="FIND_DECISION_MAKER"):
        writer.draft(ids[0])


def test_followup_and_wrong_contact(
    engine: Engine, writer: OutreachDraftService, ids: tuple[UUID, ...]
) -> None:
    initial = writer.draft(ids[0], OutreachChannel.EMAIL).draft
    with pytest.raises(ValueError, match="Follow-up"):
        writer.draft(ids[0], OutreachChannel.EMAIL, OutreachPurpose.FOLLOW_UP)
    old = activity(engine, ids[0], initial.contact_id, InteractionType.EMAIL)
    followup = writer.draft(ids[0], OutreachChannel.EMAIL).draft
    assert (
        followup.purpose == OutreachPurpose.FOLLOW_UP and followup.previous_interaction_id == old.id
    )
    assert "contacto anterior" in followup.body and "ocupado" not in followup.body
    with pytest.raises(ValueError, match="matching previous"):
        writer.draft(ids[0], OutreachChannel.PHONE_SCRIPT)


def test_accepted_connection_message(
    engine: Engine, writer: OutreachDraftService, ids: tuple[UUID, ...]
) -> None:
    invitation = writer.draft(ids[0]).draft
    activity(
        engine,
        ids[0],
        invitation.contact_id,
        InteractionType.LINKEDIN_CONNECTION,
        outcome="ACCEPTED",
    )
    message = writer.draft(ids[0]).draft
    assert not message.connection and len(message.body) <= 700
    assert "Gracias por aceptar" in message.body
    assert message.previous_interaction_id


@pytest.mark.parametrize("outcome", [None, "PENDING", "REJECTED"])
def test_no_invented_acceptance(
    engine: Engine, writer: OutreachDraftService, ids: tuple[UUID, ...], outcome: str | None
) -> None:
    invitation = writer.draft(ids[0]).draft
    activity(
        engine, ids[0], invitation.contact_id, InteractionType.LINKEDIN_CONNECTION, outcome=outcome
    )
    with pytest.raises(ValueError):
        writer.draft(ids[0], purpose=OutreachPurpose.POST_CONNECTION_MESSAGE)


def test_approval_use_are_only_human_events(
    engine: Engine, writer: OutreachDraftService, ids: tuple[UUID, ...]
) -> None:
    draft = writer.draft(ids[0], OutreachChannel.EMAIL).draft
    with pytest.raises(ValueError):
        writer.transition(draft.id, DraftStatus.USED)
    approved = writer.transition(draft.id, DraftStatus.APPROVED)
    assert approved.approved_at and approved.status == DraftStatus.APPROVED
    used = writer.transition(draft.id, DraftStatus.USED)
    assert used.used_at and len(used.events) == 2
    with SqlAlchemyUnitOfWork(engine) as uow:
        assert not uow.repository.list(LeadInteraction)
        assert uow.repository.get(OutreachDraft, draft.id) == draft
    with pytest.raises(ValueError):
        writer.transition(draft.id, DraftStatus.APPROVED)


@pytest.mark.parametrize(
    "channel,kind",
    [
        (OutreachChannel.LINKEDIN, InteractionType.LINKEDIN_CONNECTION),
        (OutreachChannel.EMAIL, InteractionType.EMAIL),
        (OutreachChannel.PHONE_SCRIPT, InteractionType.PHONE_CALL),
    ],
)
def test_explicit_record_use(
    engine: Engine,
    writer: OutreachDraftService,
    ids: tuple[UUID, ...],
    channel: OutreachChannel,
    kind: InteractionType,
) -> None:
    draft = writer.draft(ids[0], channel).draft
    writer.transition(draft.id, DraftStatus.APPROVED)
    used = writer.transition(draft.id, DraftStatus.USED, record_interaction=True)
    with SqlAlchemyUnitOfWork(engine) as uow:
        record = uow.repository.get(LeadInteraction, used.events[-1].interaction_id or uuid4())
        assert record is not None and record.interaction_type == kind
        assert record.contact_id == draft.contact_id and str(draft.id) in (record.notes or "")
        assert record.outcome is None and "not verified" in (record.notes or "")
    with pytest.raises(ValueError, match="NO_ACTION"):
        writer.draft(ids[0], force=True)


def test_reject_reason_and_history(writer: OutreachDraftService, ids: tuple[UUID, ...]) -> None:
    draft = writer.draft(ids[0]).draft
    with pytest.raises(ValueError, match="reason"):
        writer.transition(draft.id, DraftStatus.REJECTED)
    rejected = writer.transition(draft.id, DraftStatus.REJECTED, "Too generic")
    assert rejected.rejected_at and rejected.events[-1].reason == "Too generic"
    new = writer.draft(ids[0], force=True)
    assert writer.show(draft.id).status == DraftStatus.REJECTED
    assert new.status == DraftStatus.DRAFT


def test_export(writer: OutreachDraftService, ids: tuple[UUID, ...]) -> None:
    view = writer.draft(ids[0], OutreachChannel.EMAIL)
    data = json.loads(export_drafts([view], OutreachFormat.JSON))
    assert data[0]["status"] == "DRAFT" and data[0]["draft"]["body"] == view.draft.body
    markdown = export_drafts([view], OutreachFormat.MARKDOWN)
    assert "## Outreach draft" in markdown and "Evidence:" in markdown
    assert str(view.draft.evidence_citations[0].source_id) in markdown
    assert view.draft.body in export_drafts([view], OutreachFormat.TEXT)


def test_quality_missing_refs_and_claims(
    writer: OutreachDraftService, ids: tuple[UUID, ...]
) -> None:
    draft = writer.draft(ids[0], OutreachChannel.EMAIL).draft
    with pytest.raises(ValidationError):
        OutreachDraft.model_validate(
            {
                **draft.model_dump(),
                "claims": (
                    GroundedClaim(text="You are losing customers", evidence_ids=(uuid4(),)),
                ),
            }
        )
    with pytest.raises(ValidationError):
        OutreachDraft.model_validate({**draft.model_dump(), "supporting_evidence_ids": (uuid4(),)})
    with pytest.raises(ValidationError):
        OutreachDraft.model_validate({**draft.model_dump(), "body": "x" * 1201})


class UnsupportedPolicy(V1OutreachPolicy):
    def render(self, **kwargs: Any) -> tuple[str | None, str]:
        return "Sales", "Your website is bad and you are losing customers."


def test_unsupported_policy_blocked_atomic(engine: Engine, ids: tuple[UUID, ...]) -> None:
    service = OutreachDraftService(lambda: SqlAlchemyUnitOfWork(engine), policy=UnsupportedPolicy())
    with pytest.raises(ValueError, match="Unsupported claim"):
        service.draft(ids[0], OutreachChannel.EMAIL)
    with SqlAlchemyUnitOfWork(engine) as uow:
        assert not uow.repository.list(OutreachDraft)


def test_rollback_and_notfound(
    engine: Engine, writer: OutreachDraftService, ids: tuple[UUID, ...]
) -> None:
    draft = writer.draft(ids[0]).draft
    with SqlAlchemyUnitOfWork(engine) as uow:
        uow.repository.add(
            OutreachEvent(draft_id=draft.id, status=DraftStatus.REJECTED, reason="Rollback")
        )
    assert writer.show(draft.id).status == DraftStatus.DRAFT
    with pytest.raises(NotFoundError):
        writer.show(uuid4())
    with pytest.raises(NotFoundError):
        writer.draft(uuid4())


def test_shortlist_skips_and_prefers_referral(
    engine: Engine, writer: OutreachDraftService, ids: tuple[UUID, ...]
) -> None:
    views, _ = writer.shortlist()
    assert views[0].draft.referral_evidence_id
    assert ids[4] not in {v.draft.lead_id for v in views}
    again, skipped = writer.shortlist()
    assert not again and skipped


def test_cli_commands(engine: Engine, ids: tuple[UUID, ...]) -> None:
    runner = CliRunner()
    database = ["--database-url", str(engine.url)]
    result = runner.invoke(
        app,
        [
            "outreach",
            "draft",
            "--lead-id",
            str(ids[0]),
            "--channel",
            "EMAIL",
            "--format",
            "json",
            *database,
        ],
    )
    assert result.exit_code == 0, result.stdout
    draft_id = json.loads(result.stdout)[0]["draft"]["id"]
    for command in ("show", "approve", "mark-used"):
        result = runner.invoke(app, ["outreach", command, "--draft-id", draft_id, *database])
        assert result.exit_code == 0, result.stdout
    result = runner.invoke(app, ["outreach", "list", "--status", "USED", *database])
    assert result.exit_code == 0 and draft_id in result.stdout
    result = runner.invoke(app, ["outreach", "draft", "--lead-id", str(ids[1]), *database])
    assert result.exit_code == 0
    data = runner.invoke(
        app, ["outreach", "list", "--status", "DRAFT", "--format", "json", *database]
    )
    new_id = json.loads(data.stdout)[0]["draft"]["id"]
    result = runner.invoke(
        app, ["outreach", "reject", "--draft-id", new_id, "--reason", "Too generic", *database]
    )
    assert result.exit_code == 0 and "REJECTED" in result.stdout
    assert runner.invoke(app, ["outreach", "--help"]).exit_code == 0


def test_settings_centralized() -> None:
    assert OutreachSettings().body_limit(OutreachChannel.LINKEDIN, True) == 300
    with pytest.raises(ValidationError):
        OutreachSettings(connection_limit=999)


def test_missing_observation_fallback(
    writer: OutreachDraftService, ids: tuple[UUID, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    for key in tuple(OBSERVATIONS):
        monkeypatch.delitem(OBSERVATIONS, key)
    draft = writer.draft(ids[0], OutreachChannel.EMAIL).draft
    assert not draft.claims and "citas" in draft.body
    assert any("hypothesis" in warning for warning in draft.warnings)


def test_force_rolls_back_supersession(
    engine: Engine,
    writer: OutreachDraftService,
    ids: tuple[UUID, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    draft = writer.draft(ids[0]).draft

    def fail_commit(self: SqlAlchemyUnitOfWork) -> None:
        raise ValueError("Synthetic commit failure")

    monkeypatch.setattr(SqlAlchemyUnitOfWork, "commit", fail_commit)
    with pytest.raises(ValueError, match="commit failure"):
        writer.draft(ids[0], force=True)
    assert writer.show(draft.id).status == DraftStatus.DRAFT
    assert len(writer.list_drafts()) == 1


def test_use_rolls_back_activity(
    engine: Engine,
    writer: OutreachDraftService,
    ids: tuple[UUID, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    draft = writer.draft(ids[0], OutreachChannel.EMAIL).draft
    writer.transition(draft.id, DraftStatus.APPROVED)

    def fail_commit(self: SqlAlchemyUnitOfWork) -> None:
        raise ValueError("Synthetic commit failure")

    monkeypatch.setattr(SqlAlchemyUnitOfWork, "commit", fail_commit)
    with pytest.raises(ValueError):
        writer.transition(draft.id, DraftStatus.USED, record_interaction=True)
    assert writer.show(draft.id).status == DraftStatus.APPROVED
    with SqlAlchemyUnitOfWork(engine) as uow:
        assert not uow.repository.list(LeadInteraction)


def test_duplicate_text_different_purpose(
    writer: OutreachDraftService, ids: tuple[UUID, ...]
) -> None:
    writer.draft(ids[0], OutreachChannel.EMAIL, OutreachPurpose.EMAIL_INTRO)
    with pytest.raises(DuplicateError):
        writer.draft(ids[0], OutreachChannel.EMAIL, OutreachPurpose.FIRST_CONTACT)


def test_referral_prioritized_before_limit(
    writer: OutreachDraftService, ids: tuple[UUID, ...]
) -> None:
    from lead_engine.domain.shortlist import ShortlistFilters

    views, _ = writer.shortlist(ShortlistFilters(limit=1))
    assert len(views) == 1 and views[0].draft.lead_id == ids[3]


def test_referral_entry_and_newer_negative(
    engine: Engine, writer: OutreachDraftService, ids: tuple[UUID, ...]
) -> None:
    draft = writer.draft(ids[0], OutreachChannel.EMAIL).draft
    e = writer.record_referral(
        ids[0],
        draft.contact_id,
        "Carlos Pérez",
        "https://fixture.example/referral",
        "Explicit recommendation",
        True,
    )
    warm = writer.draft(ids[0], OutreachChannel.EMAIL, force=True).draft
    assert warm.referral_evidence_id == e.id
    with SqlAlchemyUnitOfWork(engine) as uow:
        uow.repository.add(
            Evidence(
                company_id=e.company_id,
                source_id=e.source_id,
                evidence_type="WARM_INTRODUCTION",
                statement="Referral withdrawn",
                raw_value={
                    "contact_id": str(draft.contact_id),
                    "referrer_name": "Carlos Pérez",
                    "recommended_contact": False,
                },
                observed_at=utc_now(),
                confidence=1,
            )
        )
        uow.commit()
    cold = writer.draft(ids[0], OutreachChannel.EMAIL, force=True).draft
    assert cold.referral_evidence_id is None and "Carlos" not in cold.body
    with pytest.raises(ValueError, match="another company"):
        writer.record_referral(
            ids[1], draft.contact_id, "Carlos", "https://fixture.example/record", "Recommendation"
        )


def test_supported_authority_ctas(
    engine: Engine, writer: OutreachDraftService, ids: tuple[UUID, ...]
) -> None:
    draft = writer.draft(ids[0], OutreachChannel.EMAIL).draft
    with SqlAlchemyUnitOfWork(engine) as uow:
        lead = uow.repository.get(Lead, ids[0])
        assert lead is not None
        source = Source(source_type=SourceType.MANUAL, url="https://fixture.example/authority")
        uow.repository.add(source)
        evidence = Evidence(
            company_id=lead.company_id,
            source_id=source.id,
            evidence_type="DECISION_MAKER_ACCESS",
            statement="Explicit authority attestation",
            raw_value={
                "contact_id": str(draft.contact_id),
                "authority_confirmed": True,
                "reachable": True,
            },
            observed_at=utc_now(),
            confidence=1,
        )
        uow.repository.add(evidence)
        uow.commit()
    CommercialScoringService(lambda: SqlAlchemyUnitOfWork(engine)).score_lead(ids[0])
    CommercialResearchService(lambda: SqlAlchemyUnitOfWork(engine)).research_lead(ids[0])
    meeting = writer.draft(ids[0], OutreachChannel.EMAIL, OutreachPurpose.MEETING_REQUEST).draft
    assert "15 minutos" in meeting.body and evidence.id in meeting.supporting_evidence_ids
    insight = writer.draft(ids[0], OutreachChannel.EMAIL, force=True).draft
    assert "comparto el análisis" in insight.body
    now = utc_now()
    negative = Evidence(
        company_id=evidence.company_id,
        source_id=evidence.source_id,
        evidence_type="DECISION_MAKER_ACCESS",
        statement="Authority no longer supported",
        raw_value={
            "contact_id": str(draft.contact_id),
            "authority_confirmed": False,
            "reachable": True,
        },
        observed_at=now,
        created_at=now,
        confidence=1,
    )
    assert not writer._authority((evidence, negative), draft.contact_id, now, 30)


def test_explicit_whatsapp_use_and_form_atomic_rejection(
    engine: Engine, writer: OutreachDraftService, ids: tuple[UUID, ...]
) -> None:
    contact_id = writer.draft(ids[0], OutreachChannel.EMAIL).draft.contact_id
    extra_channel(engine, ids[0], contact_id, "CONTACT_PUBLIC_WHATSAPP")
    draft = writer.draft(ids[0], OutreachChannel.WHATSAPP).draft
    writer.transition(draft.id, DraftStatus.APPROVED)
    used = writer.transition(draft.id, DraftStatus.USED, record_interaction=True)
    with SqlAlchemyUnitOfWork(engine) as uow:
        interaction = uow.repository.get(LeadInteraction, used.events[-1].interaction_id or uuid4())
        assert interaction and interaction.interaction_type == InteractionType.WHATSAPP
    contact_id = writer.draft(ids[1], OutreachChannel.EMAIL).draft.contact_id
    extra_channel(engine, ids[1], contact_id, "CONTACT_PUBLIC_CONTACT_FORM")
    form = writer.draft(ids[1], OutreachChannel.WEBSITE_CONTACT_FORM).draft
    writer.transition(form.id, DraftStatus.APPROVED)
    with pytest.raises(ValueError, match="no matching interaction type"):
        writer.transition(form.id, DraftStatus.USED, record_interaction=True)
    assert writer.show(form.id).status == DraftStatus.APPROVED
    assert writer.transition(form.id, DraftStatus.USED).status == DraftStatus.USED


def test_additive_migration_preserves_baseline(tmp_path: Any) -> None:
    from alembic import command
    from alembic.autogenerate import compare_metadata
    from alembic.runtime.migration import MigrationContext
    from sqlalchemy import inspect

    from lead_engine.infrastructure.database import build_engine
    from lead_engine.infrastructure.orm import Base
    from lead_engine.infrastructure.schema import migration_config, upgrade_database

    url = f"sqlite+pysqlite:///{tmp_path / 'outreach-migration.db'}"
    engine = build_engine(url)
    with engine.begin() as connection:
        config = migration_config(url)
        config.attributes["connection"] = connection
        command.upgrade(config, "0006")
    source = Source(source_type=SourceType.MANUAL, title="Existing source")
    with SqlAlchemyUnitOfWork(engine) as uow:
        uow.repository.add(source)
        uow.commit()
    upgrade_database(url)
    with SqlAlchemyUnitOfWork(engine) as uow:
        assert uow.repository.get(Source, source.id) == source
    with engine.connect() as connection:
        assert not compare_metadata(MigrationContext.configure(connection), Base.metadata)
    with engine.begin() as connection:
        config = migration_config(url)
        config.attributes["connection"] = connection
        command.downgrade(config, "0006")
    assert "outreach_drafts" not in inspect(engine).get_table_names()
    upgrade_database(url)
    with SqlAlchemyUnitOfWork(engine) as uow:
        assert uow.repository.get(Source, source.id) == source
    engine.dispose()


def test_ambiguous_referral_never_invented(writer: OutreachDraftService) -> None:
    now = utc_now()
    contact_id = uuid4()
    positive = Evidence(
        company_id=uuid4(),
        source_id=uuid4(),
        evidence_type="REFERRAL_PATH",
        statement="Recommendation",
        observed_at=now,
        created_at=now,
        confidence=0.9,
        raw_value={
            "contact_id": str(contact_id),
            "referrer_name": "Carlos",
            "recommended_contact": True,
        },
    )
    negative = Evidence.model_validate(
        {
            **positive.model_dump(),
            "id": uuid4(),
            "raw_value": {
                "contact_id": str(contact_id),
                "referrer_name": "Carlos",
                "recommended_contact": False,
            },
        }
    )
    assert writer._referral((positive, negative), contact_id, now) is None
    other = Evidence.model_validate(
        {
            **positive.model_dump(),
            "id": uuid4(),
            "raw_value": {
                "contact_id": str(contact_id),
                "referrer_name": "Ana",
                "recommended_contact": True,
            },
        }
    )
    assert writer._referral((positive, other), contact_id, now) is None


def test_stale_human_event_cannot_overwrite_decision(
    engine: Engine, writer: OutreachDraftService, ids: tuple[UUID, ...]
) -> None:
    draft = writer.draft(ids[0]).draft
    writer.transition(draft.id, DraftStatus.APPROVED)
    with pytest.raises(DuplicateError):
        with SqlAlchemyUnitOfWork(engine) as uow:
            uow.repository.add(
                OutreachEvent(
                    draft_id=draft.id,
                    sequence=1,
                    status=DraftStatus.REJECTED,
                    reason="Stale decision",
                )
            )
            uow.commit()
    assert writer.show(draft.id).status == DraftStatus.APPROVED
