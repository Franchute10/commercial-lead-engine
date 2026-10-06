# Commercial Research Analyst V1

A commercial brief helps a human decide whether, why and how to approach a company. It consolidates
stored facts, not aesthetic judgments or conversion-performance estimates. Missing data is not
negative evidence. No LLM/API, live search, new scraping, automatic scoring or outreach is involved.
An opportunity is a hypothesis to discuss, not a claim that the business needs a new website.

## Architecture and immutable history

CommercialResearchService reads lead/company/campaign, Source-backed Evidence, WebsiteAudit and the
latest LeadScore through repository/UoW ports, and reuses DecisionMakerFinder via the typed
DecisionMakerRecommendations port. Replaceable ResearchPolicy implementations receive a frozen
ResearchContext and return a validated CommercialBrief. Domain code imports no application or
infrastructure. Built-in versions are health-research-v1, construction-research-v1 and
hospitality-research-v1. Shared evidence resolution uses the existing Observations utility;
research completeness and status are computed independently of score completeness.

Every generation inserts a new brief. Migration 0005 adds commercial_briefs without altering existing
tables, contacts or evidence. Relational indexed identity/time/version headers and a nullable score FK
support filtering, while a typed JSON snapshot preserves the complete brief, embedded contacts,
source citations and exact templates. JSON serialization retains UUIDs, timestamps and nested models.
There is no update/delete brief command. Historical reads and exports never recompute policies.
Source title/URL/statement/observation timestamps are embedded so later source changes cannot rewrite
the exported history. The service rejects policy-generated references outside the company snapshot,
wrong lead/version/score identities and citations differing from the stored source/evidence.

Each lead generation commits atomically; a failure after flush rolls back. Campaign generation is
sequential and each lead commits independently. No lead status/priority, evidence, score or contact is
modified. A campaign failure may leave earlier completed briefs in history; retrying appends new ones.
Production PostgreSQL mappings are portable, but live PostgreSQL verification remains future work.

## Opportunity taxonomy and rules

The taxonomy includes APPOINTMENT_CONVERSION, RESERVATION_CONVERSION, QUOTE_CONVERSION,
PRODUCT_DISCOVERY, SERVICE_DISCOVERY, CATALOG_IMPROVEMENT, B2B_LEAD_CAPTURE, PRIVATE_EVENTS,
CUSTOMER_JOURNEY, DIGITAL_TRUST, CONTACTABILITY, ECOMMERCE, BRAND_EXPERIENCE, LOCAL_DISCOVERY,
MULTI_LOCATION_EXPERIENCE and NO_CLEAR_OPPORTUNITY.

Each real opportunity requires at least one eligible explicit commercial anchor and one eligible
explicit gap, with at least two distinct Evidence UUIDs. Anchors require boolean true; unsupported
notes, company industry alone, a high score alone and contact presence alone never create opportunities.
The first known gap signal in each ordered rule is authoritative; a positive aggregate path prevents
using a missing CTA to claim no path, and an uncertain aggregate stops that rule. A missing signal
cannot be used as false. A supported opportunity has a title, deterministic reasons, strength and
source-backed evidence IDs. Select one primary and at most three secondary opportunities by descending
rule strength, with the documented rule order as deterministic tie-break. No arbitrary severity or
probability is inferred.

| Campaign | Ordered rules: commercial anchors → observed gap | Strength |
|---|---|---:|
| HEALTH | High-value service → reservation/appointment path, then booking CTA | 3 |
| HEALTH | High-value service → service-discovery path | 2 |
| HEALTH | High-value service or corporate clients → direct-contact path | 2 |
| CONSTRUCTION | B2B, distribution network or high-value product → quote path, then quote CTA | 3 |
| CONSTRUCTION | Distribution network or high-value product → catalog link | 2 |
| CONSTRUCTION | High-value product or distribution network → product-discovery path | 2 |
| CONSTRUCTION | B2B or corporate clients → direct-contact path, then contact form | 2 |
| HOSPITALITY | Private events or active Instagram → reservation path, then booking CTA | 3 |
| HOSPITALITY | Private events → direct-contact path, then contact form | 2 |
| HOSPITALITY | Active Instagram → catalog/menu link | 2 |
| HOSPITALITY | Private events or multiple locations → direct-contact path | 2 |

Stored rating >=4.3 AND review count >=100 provide an alternative reputation anchor for the campaign's
primary conversion opportunity only when the corresponding aggregate path is explicitly absent.
Both reputation observations and the gap are cited. Existing conversion opportunities also cite
corroborating reputation, and the primary opportunity adds a recent-expansion reason and references
when explicitly supported, without increasing rule strength. Reputation alone does not prove demand or
conversion loss. General rules follow campaign rules: campaign anchor + missing privacy link
(DIGITAL_TRUST, strength 1); campaign anchor + missing contact path (CONTACTABILITY, 2); explicit
ECOMMERCE + missing ecommerce path (2); explicit MULTIPLE_LOCATIONS + missing map link
(LOCAL_DISCOVERY, 1) or missing direct-contact path (MULTI_LOCATION_EXPERIENCE, 2).

Observations require confidence >=0.7 and eligible dates. Business/reputation signals use the existing
365-day window; activity signals 90 days; growth signals 180 days; website signals/audits 30 days.
Future data is excluded. Contradictions require strictly newer equally/higher-confidence evidence;
otherwise the fact remains UNCERTAIN and the brief warns. Website absence requires an explicit manual
boolean or coverage from a successful current audit. Failed/partial/older positive-only audits cannot
make unobserved findings false. Static absence describes only the inspected scope, not the entire
website, booking platforms or actual commercial performance.

## Summary and contact angle

Company summary uses recorded name, industry, city/country and website plus at most two resolved
commercial anchors. Long stored values are clipped to keep the summary bounded; no company scale,
specialty, traffic, revenue or ownership is invented. A reputation sentence is included only when
both numeric thresholds are evidenced. Templates are fixed by the selected opportunity, for example:

- Appointment: explore simplifying the observed digital path to evaluations and appointments.
- Quote: explore simplifying product discovery and quotation requests.
- Reservation: explore simplifying reservation requests in the observed digital experience.
- Private events: connect the documented offer to a clearer inquiry path.

No clear opportunity uses a clarification angle: establish business goals and missing evidence before
proposing an improvement. These are conversation themes, not outbound messages or scripts.

## Contacts and missing information

Reuse up to three Finder recommendations including name, current role/category, fit/confidence,
verification, public channels and Evidence references. SUPPORTED contacts count as currently available;
STALE/CONFLICTED contacts remain displayed with warnings but do not count as current availability.
Finder role targets are preserved when no supported individual exists. Availability does not prove
purchasing authority or reachability; human verification and approval remain necessary.

Unknown industry/city/country, advertising, review count/rating, expansion, multiple locations,
booking provider and campaign anchors are explicitly listed. Current audit/score/contact availability
and owner/founder identification are tracked too. An explicit false is known and is not mislabeled
unknown. A missing booking-provider observation does not claim that no provider exists. Missing
information lists are not intended to infer weaknesses or award opportunity points.

## Separate research completeness

Completeness is the sum of the following explainable categories, with referenced evidence where
applicable. It measures tracked knowledge, not sales likelihood or data truthfulness.

| Category | Maximum | Award rule |
|---|---:|---|
| Company basics | 15 | 5 each: recorded name; industry; city AND country |
| Website audit | 20 | Current SUCCESS or explicit NO_WEBSITE record; otherwise 0 |
| Commercial signals | 25 | floor(25 × resolved campaign anchors / all campaign anchors) |
| Reputation | 10 | 5 each for resolved review count and rating |
| Contact research | 20 | 20 current supported person; 10 stale/conflicted supported history; 0 role fallback |
| Score available | 10 | 10 current score; 5 historical/outdated score; 0 absent |

Campaign anchor sets are HEALTH (high-value service, corporate clients), CONSTRUCTION (high-value
product, distribution network, B2B, corporate clients) and HOSPITALITY (private events, active Instagram,
multiple locations). Explicit false contributes knowledge, but never counts as a positive anchor.
Research completeness does not copy, normalize or reuse LeadScore's completeness.

A current score is at most 30 days old, has no newer stored commercial evidence (except free-text
notes), and has no newer eligible audit. Rescoring is never automatic. New briefs use the latest score
and recorded score bands; older briefs retain their original score ID/value/band/date. Historical
scores remain displayed with a rescore warning and contribute only 5 completeness points.

## Status and priority

- INSUFFICIENT_DATA: no evidenced opportunity; type NO_CLEAR_OPPORTUNITY and priority NONE.
- PARTIAL: opportunity exists but READY conditions are unmet.
- READY: opportunity, current score, current successful/explicit no-website audit, at least two
  positive campaign anchors and completeness >=70. A target-role fallback is allowed.

Priority is separate from status. With an opportunity: LOW by default; MEDIUM requires current score
>=55 and completeness >=50; HIGH requires current score >=70, completeness >=70, rule strength 3
and a current supported decision maker. READY describes evidence readiness, not a score threshold or
permission to contact. Availability, status, completeness, primary opportunity and pipeline snapshot
are exposed for a future Daily Shortlist. No shortlist or scheduling is implemented here.

## CLI and exports

```powershell
python -m lead_engine db init
python -m lead_engine research lead --lead-id LEAD_UUID
python -m lead_engine research campaign --campaign "Salud Chiclayo" --min-score 70 --limit 10
python -m lead_engine research show --lead-id LEAD_UUID
python -m lead_engine research show --lead-id LEAD_UUID --format markdown
python -m lead_engine research show --lead-id LEAD_UUID --format json
python -m lead_engine research list --campaign "Salud Chiclayo"
python -m lead_engine research history --lead-id LEAD_UUID --format json
python -m lead_engine research campaign --campaign "Salud Chiclayo" --export briefs.json
python -m lead_engine research show --lead-id LEAD_UUID --export brief.md
python -m lead_engine research show --lead-id LEAD_UUID --export summary.csv
python -m examples.research_fixture_demo
```

All commands support --database-url / LEAD_ENGINE_DATABASE_URL. Campaign names resolve exactly or by
UUID; ambiguity fails. Batch defaults to 5 leads, maximum 100, ordered by latest score then lead UUID.
A supplied --min-score filters by the latest stored score; unscored leads do not qualify. It does not
regenerate scores or require that historical scores be current; the generated brief makes this clear.
Every selected lead gets a fresh historical brief. List returns the latest saved brief per campaign
lead; show/history only read saved snapshots.

Markdown is human-readable and includes reasons, contacts, missing information, category points and
Source citations. JSON is an array of complete structured briefs, including single-brief output.
CSV is a one-row-per-brief summary for future shortlist inputs. --format controls stdout; --export
uses .md/.json/.csv suffix to select format. Export targets must be new files; existing files are not
overwritten. UTF-8 is used. Stored source text is escaped in Markdown and spreadsheet formula prefixes
are neutralized in CSV. No DOCX/PDF output is added. Export failure does not undo already saved briefs.

## Offline acceptance

The demo reuses synthetic scoring/audit fixtures, adds attributed manual contact observations and
scores before generating briefs. No network is called.

| Fixture | Score | Primary opportunity | Completeness | Status / Priority |
|---|---:|---|---:|---|
| Health clinic | 91 / A | APPOINTMENT_CONVERSION | 100% | READY / HIGH |
| Construction distributor | 97 / A | QUOTE_CONVERSION | 83% | READY / HIGH |
| Hospitality restaurant | 96 / A | RESERVATION_CONVERSION | 91% | READY / HIGH |
| Low-data lead | 0 / E | NO_CLEAR_OPPORTUNITY | 35% | INSUFFICIENT_DATA / NONE |

The first three include current contacts, all opportunities cite stored source-backed evidence,
unknowns remain explicit, and regenerating preserves the earlier brief. Automated tests cover policy
selection, conflicts/freshness, history/rescoring, provenance validation, rollback, exports and CLI.
