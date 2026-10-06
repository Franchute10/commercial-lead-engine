# Commercial Scorer V1

The score estimates commercial interest as a prospect for digital/customer-acquisition improvement.
It is a documented prioritization heuristic, not a website quality grade, conversion forecast or
probability of a sale. There is no AI inference, subjective visual assessment or automatic outreach.
A missing website alone is insufficient: all opportunity rules require evidence of commercial activity.

## Policy contract and history

CommercialScoringService loads the lead, campaign, company, attributed evidence, contacts and audits
through repository/UnitOfWork ports. CampaignScoringPolicy is replaceable; V1Policy is an immutable
set of dimensions/rules. Built-ins are `health-v1`, `construction-v1`, `hospitality-v1`.
Each policy maximum is exactly 100. Every invocation persists a new LeadScore and ScoreComponents;
no history is overwritten. Scoring never changes lead status, priority, contact state or outreach approval.

Scores retain policy version, calculation cutoff, band thresholds, completeness, selected audit ID,
component points/explanations and evidence references. Presentation metadata lives as structured JSON
in the existing LeadScore explanation field, so no new migration/table is necessary. ScoreComponents
retain their relational evidence links. Older manually recorded scores without metadata display
UNKNOWN completeness and default presentation bands, without rewriting their records.

For reproduction, use the same version, historical evidence/contact/audit records and calculation
cutoff. Evidence observation and creation must not be after that cutoff. Company URL changes require
an audit for the current configured URL; the selected audit ID is recorded. Scores are historical
snapshots: editing the policy, company/contact interpretation, rule thresholds or confidence/age
semantics requires a new policy version, retaining previous policy implementations. New versions can
be registered with the application service; no orchestration framework is involved.

## Weights and exact rules

Each listed boolean business rule awards its entire weight only for resolved explicit true evidence.
False earns zero but is known; missing/uncertain earns zero and is unknown for readiness.
Weights retain the task's suggested dimensions. The subrules make them independently testable.
Points are coarse integers in V1, calculated with Decimal to preserve exact totals.

### health-v1

| Dimension | Max | Subrules |
| --- | ---: | --- |
| COMMERCIAL_VALUE | 20 | HIGH_VALUE_SERVICE 12; CORPORATE_CLIENTS 4; MULTIPLE_LOCATIONS 4 |
| DIGITAL_ACTIVITY | 10 | INSTAGRAM_ACTIVE 4; FACEBOOK_ACTIVE 3; PAID_ADVERTISING_ACTIVE 3 |
| REPUTATION_SIGNAL | 10 | Review-count tier up to 8; rating up to 2 |
| WEBSITE_OPPORTUNITY | 25 | NO_WEBSITE 5; viewport absent 8; meta description absent 4; HTTPS missing 4; slow response 4 |
| CONVERSION_OPPORTUNITY | 20 | Reservation path absent 8; contact form absent 6; WhatsApp link absent 6 |
| DECISION_MAKER_ACCESS | 10 | Verified reachable role tier up to 10 |
| GROWTH_SIGNAL | 5 | RECENT_EXPANSION 3; ACTIVE_HIRING 2 |

### construction-v1

| Dimension | Max | Subrules |
| --- | ---: | --- |
| COMMERCIAL_SCALE | 20 | DISTRIBUTION_NETWORK 8; B2B_OPERATION 6; HIGH_VALUE_PRODUCT 6 |
| DIGITAL_ACTIVITY | 10 | INSTAGRAM_ACTIVE 4; FACEBOOK_ACTIVE 3; PAID_ADVERTISING_ACTIVE 3 |
| CATALOG_OPPORTUNITY | 15 | Product discovery path absent 10; catalog/menu link absent 5 |
| QUOTATION_OPPORTUNITY | 20 | Quote path absent 12; contact form absent 8 |
| WEBSITE_OPPORTUNITY | 15 | NO_WEBSITE 3; viewport absent 6; meta description absent 3; HTTPS missing 3 |
| DECISION_MAKER_ACCESS | 10 | Verified reachable role tier up to 10 |
| GROWTH_SIGNAL | 10 | RECENT_EXPANSION 4; NEW_LOCATION 3; ACTIVE_HIRING 3 |

### hospitality-v1

| Dimension | Max | Subrules |
| --- | ---: | --- |
| BRAND_ACTIVITY | 15 | INSTAGRAM_ACTIVE 6; FACEBOOK_ACTIVE 4; PRIVATE_EVENTS 5 |
| REPUTATION_SIGNAL | 15 | Review-count tier up to 12; rating up to 3 |
| RESERVATION_OPPORTUNITY | 20 | Reservation path absent 12; booking action absent 8 |
| WEBSITE_OPPORTUNITY | 20 | NO_WEBSITE 4; viewport absent 8; meta description absent 4; HTTPS missing 4 |
| EXPERIENCE_OPPORTUNITY | 15 | Direct contact path absent 8; map link absent 4; hours pattern absent 3 |
| DECISION_MAKER_ACCESS | 10 | Verified reachable role tier up to 10 |
| GROWTH_SIGNAL | 5 | NEW_LOCATION 3; RECENT_EXPANSION 2 |

All opportunity subrules are gated by a commercial anchor: at least one resolved true observation of
HIGH_VALUE_SERVICE, HIGH_VALUE_PRODUCT, MULTIPLE_LOCATIONS, PRIVATE_EVENTS, ECOMMERCE,
DISTRIBUTION_NETWORK, B2B_OPERATION, CORPORATE_CLIENTS or EXPORT_ACTIVITY; alternatively a resolved
review count >=10. The gate's evidence is cited alongside the opportunity observation. Social activity
alone, company name, industry label, contact existence or website absence do not establish this anchor.
If every anchor input is explicitly known negative, the gate is ABSENT and opportunities still earn
zero; otherwise missing anchor support is UNKNOWN. There is no renormalization to compensate for
unknown subrules.

### Reputation and role thresholds

| Resolved Google review count | Health points (max 8) | Hospitality points (max 12) |
| --- | ---: | ---: |
| <10 | 0 | 0 |
| 10–49 | 2 | 2 |
| 50–199 | 4 | 6 |
| 200–499 | 6 | 10 |
| >=500 | 8 | 12 |

These are 20%, 50%, 80%, 100% weight buckets, rounded half-up to whole points. They are rough
reputation-volume indicators, not a claim that reviews are genuine or represent market share.
Rating requires resolved review count >=10. Rating >=4.5 awards its full weight; 4.0–4.49 awards
half rounded up (Health 1, Hospitality 2); lower ratings award zero. Without eligible review-count
evidence, rating is withheld and not counted as a ready rule.

Decision-maker evidence is structured JSON with existing contact_id, authority_confirmed and reachable.
Both booleans must be true to earn points. The contact must belong to the lead company. OWNER,
FOUNDER and GENERAL_MANAGEMENT award 10; COMMERCIAL, MARKETING, DIGITAL and CUSTOMER_EXPERIENCE
award 8; MEDICAL_DIRECTOR awards 8 only in Health; ADMINISTRATION awards 4; UNKNOWN/other cases 0.
A name or contact role alone earns no points. Role means the category stored on that contact; the
manual observation explicitly attests authority/access and must have its own source. Contacts do
not automatically become decision makers. Observations resolve per contact; the strongest resolved
verified reachable role is used, so different legitimate people are not treated as contradictions.

## UNKNOWN, PRESENT, ABSENT and contradictions

Explicit boolean false is ABSENT. True is PRESENT. Numeric observations are PRESENT even when zero
(the measurement is known). Missing, stale, malformed or confidence <0.7 evidence is UNKNOWN.
Unresolved contradictory eligible values are UNCERTAIN. Unknown/uncertain does not incur a penalty
or become a negative finding; it earns no unsupported points and lowers readiness.

For equal observed values, results are stable regardless of repository order. For contradictory
eligible values, a newer observation wins only if its timestamp is strictly newer than every differing
observation and its confidence is at least theirs. Otherwise points are withheld as UNCERTAIN.
All considered evidence IDs, including losing observations, remain cited; templated explanations
state the resolution. Low-confidence observations do not override eligible higher-confidence facts.
Repeated observations do not accumulate points.

Eligibility windows, fixed in V1: activity/advertising 90 days; growth/rebrand/decision-maker access
180 days; reputation/commercial structure 365 days; website/manual website observations 30 days.
No future-dated observation or evidence created after the calculation cutoff contributes.

Website scoring uses the latest completed audit for the currently configured URL, within 30 days.
A later FAILED/PARTIAL audit does not silently fall back to an older successful one. Only SUCCESS
supports measured HTML signals; NO_WEBSITE supports the factual company-record absence only.
Failure/timeout/access denial is not evidence that a conversion path is absent.

New successful HTML audits record WEBSITE_SIGNAL_COVERAGE (`static-homepage-v1`) with explicit booleans
for the tested static markup signals. False in that map supports ABSENT; positive findings support
PRESENT. A missing positive finding in an older audit without coverage remains UNKNOWN. A manual
boolean observation with provenance can also explicitly establish presence/absence. HTTPS is the
observed transport scheme; slow response uses measured final response time >=3000ms. Broken links,
JavaScript behavior and actual form/reservation functionality are not inferred.

These rules identify opportunities for review, not universal requirements. For example, absence of
a contact form may be deliberate where phone works well; the score does not prove business harm.

## Bands and data completeness

| Band | Score | Presentation |
| --- | --- | --- |
| A | 85–100 | High priority |
| B | 70–84 | Strong prospect |
| C | 55–69 | Worth reviewing |
| D | 40–54 | Low priority |
| E | 0–39 | Do not prioritize |

Bands are presentation labels; numeric score/evidence remain authoritative. Thresholds are stored
per score and configurable on a policy. Custom thresholds require a new version (not a built-in -v1).

Completeness = sum of maximum point weights for subrules with resolved eligible inputs, including
required gate/dependencies, divided by the 100-point policy maximum. Therefore known_weight is also
the percentage. An explicit false can count as known readiness while earning 0; uncertain/missing
rules contribute neither points nor readiness. This is weighted scoring-input coverage, not a share
of all possible business facts, number of sources or confidence/probability of success. Opportunity
rules with an unknown business anchor are not ready even when a website check exists. A provisional
no-website control can therefore score 0 with 0% readiness. Completeness never inflates the score.

## CLI and structured observations

```powershell
python -m lead_engine evidence add --company-id COMPANY_UUID --type GOOGLE_REVIEW_COUNT --value 427 --value-type integer --source manual
python -m lead_engine evidence add --company-id COMPANY_UUID --type GOOGLE_RATING --value 4.7 --value-type decimal --source manual
python -m lead_engine evidence add --company-id COMPANY_UUID --type INSTAGRAM_ACTIVE --value true --value-type boolean --source manual
python -m lead_engine evidence add --company-id COMPANY_UUID --type COMMERCIAL_NOTE --value "Public observation" --value-type text --source manual
python -m lead_engine score lead --lead-id LEAD_UUID
python -m lead_engine score campaign --campaign "Salud Chiclayo" --min-score 70
python -m lead_engine score list --campaign "Salud Chiclayo" --band A
python -m lead_engine score explain --lead-id LEAD_UUID
python -m lead_engine score history --lead-id LEAD_UUID
```

All commands support database URL overrides. --source accepts manual or an existing Source UUID;
new manual sources can use --source-url. --observed-at requires a timezone; default is current UTC.
--confidence defaults to 0.8 and must be within 0–1. Decimal observations use canonical decimal strings
in JSON raw_value (avoiding binary float loss). Booleans must be literal true/false. JSON supports
structured decision-maker observations with the existing contact UUID. Free text COMMERCIAL_NOTE
is retained as provenance but never earns points. Invalid observations roll back their new source.

Supported commercial types include all task-listed signals plus DECISION_MAKER_ACCESS/COMMERCIAL_NOTE.
Some establish the anchor or remain stored for future policies without directly receiving points;
for example RECENT_REBRAND is not rewarded in V1. Manual website flags accept explicit booleans;
users must actually observe the fact. They must not infer false from an unknown field.

Campaign scoring creates history for every lead; --min-score filters output only. List shows the
latest score per lead; explain reads the latest stored score without rescoring; history lists all
records in chronological order. It never contacts anyone or advances pipeline states.

## Offline acceptance

`python examples/scoring_fixture_demo.py` creates four synthetic leads and attributed signals,
audits deterministic homepage HTML without network, scores/rescores and verifies preserved history.
Health scores 91, Construction 97, Hospitality 96; each has 100% policy input coverage. Sparse control
scores 0 with 0% coverage. All awarded components cite evidence. Temporary data is removed afterward.
These fixture scores demonstrate rule behavior; they are not real company evaluations.
