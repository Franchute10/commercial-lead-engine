# Human-Approved Outreach Writer V1

Outreach is conversation preparation around an evidenced commercial opportunity, never a pitch
for “a website”, a judgment of design quality, or an assertion of unmeasured lost revenue.
V1 is deterministic, local and requires neither an LLM nor a paid service/API key. No transport,
social automation, clipboard integration, attachment generation or scheduling exists.

## Architecture and provenance

`OutreachDraftService` reads current leads, company/campaign, latest aligned brief/score/audit,
stored supported contacts, public channels, evidence/sources and company-wide interactions. It
uses the same `ShortlistPolicy` and configurable cooldowns as the daily shortlist. The domain
`OutreachPolicy` contract has one closed, validated `outreach-v1` renderer. A custom renderer
cannot inject arbitrary unsupported prose under the V1 identifier. A future policy version must
introduce its own quality rules explicitly. Business logic has no infrastructure imports.

Personalized business observations come from an allowlist of positive structured facts, resolved
by the shared `Observations` confidence/conflict/freshness rules. Only facts supported by the
brief's evidence are used. Source IDs, URLs when recorded, observation timestamps, evidence IDs
and the original statements are saved as citations; each rendered observation has a `GroundedClaim`
with its exact text and references. The supported role, public contact channel and primary
opportunity also carry evidence references. Free-text source statements are never pasted into
message bodies. Missing observation coverage falls back to a translated hypothesis about the
brief's opportunity, with a warning; it does not invent a positive or negative observation.

Every reference must exist, belong to the company and precede the preparation cutoff. Referral
and authority evidence use fresh, person-specific structured records. A role title alone does
not prove buying authority. Conflicting authority at the latest observation time routes the CTA
back to asking who handles the topic. Current shortlist eligibility still applies to fallback
messages: missing contact, stale research or suppressed leads never acquire a fake draft.

## Campaign and role policies

The primary opportunity selects a specific angle: appointments, quotations, reservations,
service/product discovery, catalog, B2B enquiries, private events, customer journey, information
clarity/trust, contactability, ecommerce, brand continuity, local discovery or locations.
Campaign defaults are health evaluations/appointments, construction product enquiries/quotations,
and hospitality reservations/event enquiries. These are proposed areas to explore, not promises
of results or claims about measured conversion.

| Role | Focus |
| --- | --- |
| OWNER / FOUNDER | Growth and the commercial process |
| GENERAL_MANAGEMENT | Commercial effectiveness and customer journey |
| MARKETING / BRAND | Campaign continuity and brand experience |
| COMMERCIAL | Capture, quotation and follow-up |
| DIGITAL / ECOMMERCE | Digital conversion and integration |
| CUSTOMER_EXPERIENCE | End-to-end customer journey |
| MEDICAL_DIRECTOR | Patient information and trust; no technical marketing language |
| ADMINISTRATION (operations) | Workflow and team handoffs |
| UNKNOWN | Customer journey and role routing |

Spanish (`es`) is the default, including Peru campaigns. English (`en`) is explicit. Names never
determine language. Owner/founder naming, role and channel are taken from supported stored
recommendations, not fabricated from domains, emails or social URLs.

## Eligibility and purposes

First contact needs an actionable current shortlist decision, a supported opportunity and
person/public channel. The current shortlist requires a READY, fresh aligned brief for initial
contact; PARTIAL remains research-first. The writer's minimum acceptable PARTIAL threshold is 70,
but cannot override a shortlist RESEARCH_MORE decision. FIND_DECISION_MAKER, RESEARCH_MORE,
NO_ACTION, REVIEW_MANUALLY and internal meeting/proposal preparation produce no outreach draft.

`PREPARE_CONTACT_FORM` is an explicit shortlist action for a supported public form and supported
person; its text addresses the company inbox, without claiming the person receives that inbox.
`--channel` re-evaluates the shortlist with that published channel as the sole preference. It
never bypasses suppressions or company-wide cooldowns. Unknown/unverified channels are rejected.

Supported purposes: FIRST_CONTACT, LINKEDIN_CONNECTION, LINKEDIN_MESSAGE, EMAIL_INTRO,
WHATSAPP_INTRO, FOLLOW_UP, POST_CONNECTION_MESSAGE, REFERRAL_INTRO, MEETING_REQUEST.
The default selects an invitation, accepted-connection message, eligible follow-up, referral or
channel introduction according to current context. First LinkedIn contact stays an invitation;
`LINKEDIN_MESSAGE`/`POST_CONNECTION_MESSAGE` require the appropriate recommendation, and
acceptance is never inferred from time. “Thanks for accepting” appears only for a recorded
ACCEPTED invitation to the same contact. No fictional familiarity or generic compliments.

Follow-up requires an expired company-wide cooldown, allowed current pipeline, and a recorded
outbound interaction at the same lead, contact and channel. The draft references that interaction
ID. A response requiring manual handling prevents follow-up. There are no repetitive scheduled
sequences or assumptions that the person was busy. Notes and elapsed time do not prove outreach.

## Channels, limits and CTAs

| Channel | Default body limit | Rules |
| --- | ---: | --- |
| LinkedIn invitation | 300 characters | Compact role/opportunity angle; no implied acceptance |
| LinkedIn message | 700 | Accepted invitation or eligible matching follow-up |
| WhatsApp | 600 | Explicit public business-facing WhatsApp evidence; never inferred from phone |
| Email | 1200 | Compact body and subject (80 characters) |
| Website contact form | 700 | Company-team greeting, published form URL |
| Phone script | 900 | Talking points, not a fictional dialogue |

All limits and duplicate/referral windows live in `OutreachSettings`, configurable by JSON with
`--config`. Limits are persisted in each draft. Length is checked before insertion; exceeding it
fails clearly instead of truncating a fact, name or referral. Invitation/message upper bounds
stay at 300/700. `--shortlist-config` accepts the same `ShortlistSettings` JSON used by shortlist;
configure both together if using customized cooldowns. No zero-cooldown setting is supported.

Unconfirmed decision authority receives a role-routing CTA. Explicitly supported authority gets
an offer to share analysis; MEETING_REQUEST can ask for 15 minutes. Invitations remain compact
and ask whether the recipient handles the area. The renderer never promises “two opportunities”
without knowing there are two, and never recommends PDFs, portfolios or attachments by default.

## Explicit referrals

`REFERRAL_PATH` or `WARM_INTRODUCTION` requires a structured JSON value:

```json
{
  "contact_id": "CONTACT_UUID",
  "referrer_name": "Carlos Pérez",
  "recommended_contact": true
}
```

This is an explicit human attestation of an actual recommendation, not a public relationship
inferred from an acquaintance. A source and explanatory statement are required. Fresh supported
records permit “Carlos Pérez me recomendó escribirte.” Invalid/stale/foreign records do not. A
newer explicit negative recommendation defeats an older positive; conflicting referrals at
the latest timestamp are withheld. Referral paths never waive
cooldowns. `outreach shortlist` prioritizes evidenced warm paths within its actionable candidate
pool (up to 100 ranked companies), before applying its requested draft limit. The saved daily
shortlist formula and score remain unchanged.

```powershell
python -m lead_engine outreach referral-add --lead-id LEAD_UUID --contact-id CONTACT_UUID --referrer "Carlos Pérez" --source-url "https://example.org/referral-record" --statement "Carlos explicitly recommended contacting this person"
```

Use `--warm-introduction` for WARM_INTRODUCTION. Do not create a record unless that recommendation
actually occurred. Missing referrals simply produce cold preparation without relationship claims.

## Immutable history and human workflow

Migration **0007** adds indexed `outreach_drafts` (immutable typed JSON content and relational
lead/contact/brief headers) and append-only `outreach_events`. Events have per-draft unique
sequence numbers, so conflicting concurrent transitions fail atomically rather than silently
reordering approval/use. Current status and approval/rejection/use timestamps are projections
of the ordered event history; the draft itself is never overwritten. Portable SQLAlchemy types,
foreign keys and migrations support a later PostgreSQL migration.

- DRAFT can become APPROVED or REJECTED (reason required).
- APPROVED can become USED or REJECTED.
- Regeneration creates a new UUID; earlier pending drafts become SUPERSEDED.
- APPROVED, REJECTED and USED historical records keep their decisions.
- Approval does not send, change pipeline, create an interaction or attest delivery.
- USED records a human statement of external use, only after approval.
- `mark-used` defaults to no interaction. `--record-interaction` explicitly adds channel activity
  with timestamp, contact, draft UUID and “delivery/response not verified”, atomically with USED.
  Invitations use LINKEDIN_CONNECTION; messages use LINKEDIN_MESSAGE; email/WhatsApp/phone use
  their actual type. A follow-up records its actual channel, preserving channel-specific cooldown.
- Contact-form use has no matching interaction vocabulary in V1, so optional activity recording
  is rejected; use `mark-used` without the flag. No false EMAIL/NOTE record is invented.

The recent duplicate window defaults to seven days for the same lead/person/channel/language and
purpose or identical text. `--force` allows a new history entry, but does not bypass eligibility,
unsupported evidence, cooldown, channel or length checks. Generation does not change pipeline
state, contacts, scores or briefs. Draft and supersession inserts share one transaction; explicit
activity and USED event inserts share one transaction. No send operation or outbound adapter exists.

## CLI and exports

```powershell
python -m lead_engine db init
python -m lead_engine outreach --help
python -m lead_engine outreach draft --lead-id LEAD_UUID
python -m lead_engine outreach draft --lead-id LEAD_UUID --channel EMAIL --language en
python -m lead_engine outreach draft --lead-id LEAD_UUID --channel EMAIL --purpose MEETING_REQUEST
python -m lead_engine outreach shortlist --campaign "Salud Chiclayo" --limit 5
python -m lead_engine outreach list --status DRAFT
python -m lead_engine outreach list --lead-id LEAD_UUID --format json
python -m lead_engine outreach show --draft-id DRAFT_UUID --format markdown --output draft.md
python -m lead_engine outreach approve --draft-id DRAFT_UUID
python -m lead_engine outreach reject --draft-id DRAFT_UUID --reason "Too generic"
python -m lead_engine outreach mark-used --draft-id DRAFT_UUID
python -m lead_engine outreach mark-used --draft-id OTHER_APPROVED_DRAFT_UUID --record-interaction
python -m lead_engine outreach draft --lead-id LEAD_UUID --force
python -m examples.outreach_fixture_demo
```

TEXT, Markdown and JSON show content, projected status, provenance and warnings; JSON includes
full event history. Files use UTF-8 and exclusive creation. Markdown escapes untrusted markup;
plain text remains ready for human review/copy. There is no clipboard automation. Export errors
do not reverse a previously committed draft. No real messages are sent by any CLI command.

## Offline acceptance

`python -m examples.outreach_fixture_demo` creates a temporary database and four draft cases:
health General Manager / appointment opportunity; construction Commercial Manager / quotation;
hospitality Brand Manager / reservation/brand journey; independent restaurant Owner / explicit
Carlos Pérez referral. Their email bodies differ materially, with role-specific focus, exact
claim evidence and a routing CTA where authority is unconfirmed. The fifth sparse research-first
lead creates no draft. Approval creates no outbound interaction; default mark-used records only
the human event. Tests also cover both languages, all roles, channel limits, malformed referrals,
missing observations, shortlist/cooldowns, purpose gates, persistence, rollback and CLI behavior.
