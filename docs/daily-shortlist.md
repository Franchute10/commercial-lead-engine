# Daily Shortlist V1

The shortlist ranks work that is actionable now, not just companies with high LeadScores. It combines
current research, explicit contactability, pipeline and interaction timing into a separate 0–100
priority. Every action is a human recommendation. No LinkedIn action, email, WhatsApp, phone call,
LLM/API, paid service or live research is executed by this module.

## Architecture and history

DailyShortlistService reads through repository/UnitOfWork ports and passes a frozen ShortlistContext
to the replaceable ShortlistPolicy. V1 is `daily-shortlist-v1`. The context includes current Lead state,
Company/Campaign, latest non-future score and brief, current Finder recommendations, evidence,
audits, interactions and manual suppressions. Finder is reused at the run's single aware cutoff;
no contact discovery or HTTP provider is invoked. This cutoff keeps ranking deterministic within
an execution. Timestamps/date displayed by default are UTC, not an implicit local midnight schedule.

Migration 0006 adds daily_shortlist_runs and shortlist_suppressions, leaving existing tables intact.
Runs store indexed timestamp/version headers and a typed JSON snapshot of filters, settings, selected
items/ranks, suppressed decisions/reasons, counts and eligible lead IDs beyond the limit. Selected
items are embedded value objects, not separate mutable entities. A run commits atomically; failures
after flush roll back. Historical reads/exports use saved snapshots without rerunning policy.
LeadScore, brief history, pipeline and interactions are never changed by ranking. Manual suppression
changes only its own records. PostgreSQL-compatible constructs are used; live PostgreSQL testing
remains future work.

## Priority formula (100 maximum)

| Dimension | Maximum | Exact V1 rule |
|---|---:|---|
| COMMERCIAL_POTENTIAL | 30 | Latest score >=85:30; >=70:25; >=55:17; >=40:8; otherwise 0 |
| RESEARCH_READINESS | 20 | Requires current aligned score/brief/audit and brief.score_current. READY completeness >=80:20; >=70:16; PARTIAL >=50:10; lower PARTIAL:5; otherwise 0 |
| CONTACT_ACTIONABILITY | 20 | Current supported/verified person plus explicit public channel: fit >=85:20; >=70:16; lower:10. Person without channel:8. Target roles only:4. Neither:0 |
| OPPORTUNITY_STRENGTH | 15 | Fresh aligned brief and current score: HIGH:15; MEDIUM:10; LOW:4; NONE:0 |
| PIPELINE_TIMING | 10 | QUALIFIED/READY_FOR_OUTREACH/RESPONDED/MEETING:10; eligible CONTACTED/PROPOSAL:8; DISCOVERED/QUALIFYING/READY_FOR_RESEARCH:6; closed or outbound cooldown:0 |
| FRESHNESS | 5 | Current score:2; aligned fresh brief:2; current successful/explicit no-website audit:1 |

No points overwrite LeadScore or commercial priority. Score band/completeness come from stored
ScoreSummary; legacy scores without valid metadata have an explicitly unknown completeness and use
85/70/55/40 band fallback. Commercial-potential thresholds above are fixed numeric V1 thresholds,
even if a stored scorer uses different band boundaries. A suppressed lead can have a high priority
score; suppression takes precedence and its next action becomes NO_ACTION.

Tie-break order is descending shortlist priority, descending latest LeadScore, descending research
completeness, normalized company name (case/accent insensitive), then lead UUID. Selected ranks are
1-based and stored with the exact ordering. The default limit is 5; configurable range is 1–100.
Eligibility is evaluated before the limit. Eligible leads beyond the limit are recorded separately,
not mislabeled as suppressed. Only the highest-ranked eligible lead per exact Company UUID
appears; other campaign leads at that company are recorded as DUPLICATE_COMPANY. No fuzzy merging
or company/contact data changes are performed.

## Freshness and research alignment

Default windows are 30 days for score, brief, audit and role/channel evidence. Use the latest finished
audit for the current recorded website. Only SUCCESS or explicit NO_WEBSITE earns audit freshness;
failed/partial observations never become negative claims. A brief must reference the latest score and
latest eligible audit. New commercial evidence after score/brief generation (free-text notes excepted
for score freshness) or a newer audit makes the corresponding data outdated. Current contact
recommendations are recomputed from stored evidence instead of trusting old embedded brief contacts.
Stale/conflicted/unverified people cannot supply an outreach channel.

Moderately stale, missing or misaligned research yields lower points, warnings and RESEARCH_MORE for
initial outreach. A score older than 90 days is suppressed as STALE_DATA by default. No automatic
rescoring, re-auditing, contact research or brief generation is performed. Score/audit/brief windows,
maximum score age, minimum score, cooldowns and channel preferences are configurable and are copied
into each run. Pipeline preparations and established proposal follow-up have their own action rules;
they still respect eligibility/suppression and are not first outreach.

## Suppression and filters

Default minimum LeadScore is 40; unscored leads do not qualify. Suppression reasons are recorded:
LOW_SCORE, INSUFFICIENT_RESEARCH, RECENT_CONTACT, PIPELINE_NOT_ACTIONABLE, WON, LOST, ARCHIVED,
NO_CLEAR_OPPORTUNITY, STALE_DATA, MANUAL_SUPPRESSION, FILTER_MISMATCH and DUPLICATE_COMPANY.

WON/LOST/ARCHIVED are always excluded by V1, including when a pipeline filter selects them. REJECTED
is excluded as PIPELINE_NOT_ACTIONABLE. An INSUFFICIENT_DATA brief or no evidenced primary opportunity
is excluded, while a missing brief on a qualifying scored lead can produce RESEARCH_MORE. Explicit
minimum research completeness, pipeline and opportunity filters produce recorded exclusions.
An expired/revoked manual suppression does not exclude a lead.

Campaign ID/name, campaign type and exact normalized city define the candidate scope; leads outside
that scope are not counted as considered or suppressed. Score, research completeness, pipeline and
opportunity filters are evaluated within the scope and remain explainable. Campaign names resolve
exactly or as UUID; ambiguity fails. Explain evaluates one lead with current default filters and optional
config, without persisting a new run. History JSON provides decisions using each original run's filters.

## Cooldowns and pipeline timing

| Recorded outbound interaction | Default cooldown |
|---|---:|
| LINKEDIN_CONNECTION | 7 days |
| LINKEDIN_MESSAGE | 7 days |
| EMAIL | 7 days |
| WHATSAPP | 7 days |
| PHONE_CALL | 3 days |
| FOLLOW_UP | 7 days |
| PROPOSAL_SENT | 10 days |

All recorded outbound events for every lead at the same exact Company UUID are checked, even
when the shortlist is scoped to one campaign. The latest note or a different-channel event cannot hide
an active cooldown. Next eligible time is the maximum expiry across outbound events; equality with
the cutoff is eligible. Cooldowns use exact elapsed UTC days, not calendar-day rounding. NOTE and
MEETING records do not reset outbound cooldowns. V1 does not model a scheduled next-follow-up date;
FOLLOW_UP uses its recorded occurrence plus the configured interval. Future-dated interaction records
are not treated as completed outreach and route otherwise eligible work to manual review.

RESPONDED and MEETING can remain visible during cooldown for internal review/preparation only;
the warning states that outbound cooldown remains active. Other stages are suppressed during cooldown.
If the last company outreach belongs to a different campaign lead, subsequent outreach requires
manual review instead of assuming that relationship applies to this opportunity. Manual suppression
is scoped to the explicitly supplied lead UUID, not inferred from its reason.
Temporary manual suppression records preserve starts/expires/reason. Unsuppress stamps revoked_at
on all active records rather than deleting history or marking a lead LOST. eligible_again_at includes
active manual expiries as well as outbound cooldowns; the saved reasons remain available after revocation.

## Next action and channels

Action precedence after suppression:

1. MEETING → PREPARE_MEETING; RESPONDED → REVIEW_MANUALLY.
2. Future-dated interactions → REVIEW_MANUALLY.
3. PROPOSAL → FOLLOW_UP only with a recorded PROPOSAL_SENT and expired cooldown; otherwise review.
4. Missing/stale/non-READY initial research → RESEARCH_MORE.
5. READY and no current supported person → FIND_DECISION_MAKER.
6. Person without a supported channel → REVIEW_MANUALLY.
7. Expired prior outreach → FOLLOW_UP. A LinkedIn invitation is special: only explicit interaction
   outcome `ACCEPTED` plus selected LinkedIn channel permits SEND_LINKEDIN_MESSAGE; otherwise review.
   Acceptance is never inferred from elapsed time. No repeat first invitation is recommended.
8. CONTACTED without recorded outreach → REVIEW_MANUALLY.
9. No recorded outreach → LinkedIn connection, email, phone call or explicit public-business WhatsApp,
   according to the selected channel. Website forms route to PREPARE_CONTACT_FORM for human draft review.

PREPARE_PROPOSAL is part of the action vocabulary for future pipeline integration, not assigned by
an inferred event in V1. All actions require human review, and none has an execution adapter.

Contacts sort by fit, normalized name and contact UUID. The highest-fitting current person with a
proven public channel is preferred; otherwise the highest-fitting current person is shown without
inventing a channel. For that person, default preference is LINKEDIN, EMAIL, PHONE,
WEBSITE_CONTACT_FORM, WHATSAPP. Multiple channels use the configured preference, then literal channel
value as deterministic tie-break. Outreach Writer V1 models explicit source-backed warm/referral paths and prefers them in its
draft queue. Shortlist priority remains unchanged; referrals cannot be inferred from free-text
notes or a suppression reason such as 'Waiting for referral'.

Selected channels must match an existing Source-backed Evidence statement, company/contact UUID,
expected evidence type, confidence >=0.7 and current dates. Current role evidence is required too.
WHATSAPP additionally requires explicit `business_facing: true`; it is never derived from a phone.
The existing Finder supplies LinkedIn/email/phone. Explicit manually recorded CONTACT_PUBLIC_WHATSAPP
and CONTACT_PUBLIC_CONTACT_FORM claims can supplement an already supported person, without changing
Finder fit or underlying contact data. For example, after identifying CONTACT_UUID:

```powershell
python -m lead_engine contact channel-add --contact-id CONTACT_UUID --type WHATSAPP --value "https://wa.me/PUBLISHED_BUSINESS_NUMBER" --source-url "https://company.example/contact" --business-facing
```

This is a human attestation of an actually published business channel, not an address-generation rule.
Never substitute a guessed number. The same command with --type WEBSITE_CONTACT_FORM records a published form URL.
The command requires an existing contact and source URL, and creates Source/Evidence atomically. Every chosen channel carries its Evidence UUID in the shortlist snapshot.

## CLI and configuration

```powershell
python -m lead_engine db init
python -m lead_engine shortlist today
python -m lead_engine shortlist today --campaign "Salud Chiclayo" --limit 5
python -m lead_engine shortlist today --type HEALTH --city Lima --min-score 70
python -m lead_engine shortlist today --min-research-completeness 70 --pipeline-status QUALIFIED
python -m lead_engine shortlist today --opportunity APPOINTMENT_CONVERSION
python -m lead_engine shortlist explain --lead-id LEAD_UUID
python -m lead_engine shortlist explain --lead-id LEAD_UUID --format json
python -m lead_engine shortlist history --format json
python -m lead_engine shortlist suppress --lead-id LEAD_UUID --days 14 --reason "Waiting for referral"
python -m lead_engine shortlist unsuppress --lead-id LEAD_UUID
python -m lead_engine shortlist today --limit 10 --format markdown --output daily-shortlist.md
python -m lead_engine shortlist today --format csv --output daily-shortlist.csv
python -m lead_engine shortlist today --format json --output daily-shortlist.json
python -m lead_engine shortlist today --config shortlist-settings.json
python -m examples.shortlist_fixture_demo
```

All commands support --database-url / LEAD_ENGINE_DATABASE_URL. Config is a local JSON
ShortlistSettings document. For example:

```json
{
  "score_freshness_days": 30,
  "brief_freshness_days": 30,
  "audit_freshness_days": 30,
  "contact_freshness_days": 30,
  "maximum_score_age_days": 90,
  "minimum_lead_score": 55,
  "cooldowns": {
    "LINKEDIN_CONNECTION": 7,
    "LINKEDIN_MESSAGE": 7,
    "EMAIL": 10,
    "WHATSAPP": 10,
    "PHONE_CALL": 3,
    "FOLLOW_UP": 7,
    "PROPOSAL_SENT": 14
  },
  "channel_preference": ["LINKEDIN", "EMAIL", "PHONE", "WEBSITE_CONTACT_FORM", "WHATSAPP"]
}
```

Partial settings files use defaults for omitted fields; a supplied cooldown map must contain all
seven outbound types, each between 1 and 365 days. No zero-cooldown override is provided. Channel
preferences must be distinct supported public types. The version identifies the formula; the saved
settings identify its exact configurable parameters.

Console/Markdown include why-now reasons, next-action rationale, warnings and suppression counts.
JSON is an array of full runs, retaining excluded items, reasons, settings and provenance references.
CSV includes selected-item summary rows only. CSV/Markdown escape untrusted strings/formula prefixes.
Output files use UTF-8 and exclusive creation; existing files are not overwritten. The selected format
controls contents, so use a matching filename suffix. Export failure does not undo a saved run.
No DOCX/PDF or scheduler is added. Explain's JSON is one decision object; other explain formats use
plain readable output rather than a run-level export.

## Offline acceptance

Six synthetic fixtures yield:

| Lead | LeadScore | Result | Shortlist priority / next action |
|---|---:|---|---|
| Health, READY, person and public LinkedIn, no outreach | 91 | Rank 1 | 100 / SEND_LINKEDIN_CONNECTION |
| Construction, READY, role fallback | 88 | Rank 2 | 75 / FIND_DECISION_MAKER |
| Hospitality, contacted yesterday | 86 | Suppressed | RECENT_CONTACT |
| Health, PARTIAL research | 75 | Rank 3 | 64 / RESEARCH_MORE |
| LOST | 95 | Suppressed | LOST |
| Manually suppressed | 91 | Suppressed | MANUAL_SUPPRESSION |

Every suppression is explained and persisted. Tests exercise thresholds, freshness, current pipeline
rather than old brief pipeline, cooldown boundaries, channel provenance, filters/ties, settings,
manual revocation, transaction rollback, additive migration, exports and CLI. Fixtures and automated
tests require no external internet. Human commercial judgment remains final.
