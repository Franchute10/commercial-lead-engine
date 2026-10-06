# Commercial Lead Engine

Local-first commercial research foundation. The engine models the gap between a company's
commercial potential and its digital/customer acquisition capability. It does not equate
opportunity with absence of a website. Important conclusions are stored as attributed evidence.

The current version provides a typed domain, SQLite persistence, Alembic migrations, application
services, Scout CSV/demo discovery, bounded website auditing, commercial scoring and a manual admin CLI.
It performs no deep crawling, external discovery API calls, AI inference,
LinkedIn automation or automatic outreach. No paid service, API key, Airtable, n8n or Streamlit is required.
Human approval remains required before outreach; interaction records only track human actions.

## Setup

Python 3.12+ is required. PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\Activate.ps1
python -m lead_engine --help
python -m lead_engine health
python -m lead_engine db init
python -m lead_engine db status
```

On macOS/Linux use `.venv/bin/python` or activate with `source .venv/bin/activate`.
`lead-engine` is also available as a console command. Health uses ephemeral SQLite and creates
no files. `db status` checks the persisted database migration revision and returns 1 if absent
or outdated. Successful commands return 0; invalid data/database operations return 1.

The default database is `lead_engine.db` in the working directory. Override it per command with
`--database-url sqlite+pysqlite:///development.db` or set `LEAD_ENGINE_DATABASE_URL`.
Do not commit database files, credentials or environment files.

## Manual admin commands

```powershell
python -m lead_engine company add --name "Example Clinic" --website https://example.com --city Lima --country Peru --industry Health
python -m lead_engine company list
python -m lead_engine campaign add --name "Health Lima" --type HEALTH --geography Lima
python -m lead_engine campaign list
```

Company add reuses an exact match and fills missing fields; known facts are preserved. Each line
of list output is a JSON record. These are development commands, not a final end-user interface.
The application service supports contact/source/evidence creation, campaign/lead creation, scores,
interactions, company retrieval and lead listing by campaign/status.

## Domain and evidence

- Company: original display/legal names, optional digital/contact/location details and timestamps.
- Contact: company relationship, role category and optional confidence; no automatic decision-maker designation.
- Source: source type, optional public URL/title/metadata and retrieval timestamp.
- Evidence: company, mandatory source, typed statement category, optional raw JSON value, confidence and observation time.
- Campaign: HEALTH, CONSTRUCTION or HOSPITALITY qualification context.
- Lead: one company in one campaign; a company can have leads in multiple campaigns.
- LeadScore and ScoreComponent: immutable historical scores with version, explanation and evidence links.
- PipelineStage: alias of LeadStatus, keeping one source of truth for progression.
- LeadInteraction: human action history, optionally linked to a contact from the same company.

Timestamps must be timezone-aware and are persisted as UTC. Confidence is 0–1; priority is a local
0–100 ordering hint. Scores use Decimal points with up to four decimal places. Component maximums
must sum to 100, awarded points must not exceed their maximums, and total_score must equal the sum
of awarded points. Criteria are unique per score. Components and evidence references have stable
ordering for round trips. Score evidence must belong to the lead's company. No scoring policy is implemented.

Evidence is separate from Company; manual company records can exist before research. Conclusions
should be recorded with evidence rather than inferred from a contact role or a pipeline status.
A source may be MANUAL without a URL; its ID and retrieval time still provide provenance.

## Exact identity and deduplication

The application identity service evaluates, in order:

1. Normalized primary domain (or the website host): lowercase, IDNA, without leading `www.`, port or path.
2. Normalized legal name + city + country.
3. Normalized canonical name + city + country.

Names are case-folded, accents and punctuation normalized, and whitespace collapsed. Original names
are retained. Location-based signals require both city and country; missing locality must not collapse
unrelated businesses. Domain matching does not collapse arbitrary subdomains to a registrable domain.
Name/location keys are deterministically hashed to keep index size bounded.

Matching records are reused, missing facts enriched, and existing facts retained. Conflicting domains
or signals pointing to different companies require explicit review instead of merging. Exact keys have
unique database constraints to protect concurrent writes. Retry a constraint conflict in a fresh
transaction after reviewing the identity. Known IDs also support enrichment of incomplete records.
With no domain and incomplete locality, a new UUID identifies a provisional company; repeated unknown
records cannot safely be deduplicated automatically. Shared corporate domains/branches may need manual
identity review in a future task. No fuzzy/AI matching is used.

## Persistence and migrations

Domain has no SQLAlchemy dependency. Application uses repository/unit-of-work ports; infrastructure
maps relational records. Callers explicitly commit a unit of work; exiting without commit rolls back.
Foreign keys are enabled on every SQLite connection. A unique company/campaign pair prevents duplicate
leads. Score evidence is a relational association with foreign keys, not an unvalidated JSON list.

`db init` applies packaged Alembic migrations to head (`0003`) and can be repeated safely.
Revision `0001` creates the core domain; `0002` adds discovery runs; `0003` adds website audits
and optional evidence links. From the repo,
the equivalent migration command is:

```powershell
python -m alembic -c alembic.ini upgrade head
python -m alembic -c alembic.ini current
```

The direct Alembic command reads `alembic.ini`; the CLI option/environment override does not modify it.
Future schema changes require new revisions, not edits to an already applied migration.

To reset a disposable local development database, close other clients and run:

```powershell
python -m lead_engine db reset --database-url sqlite+pysqlite:///development.db
```

Reset requires interactive confirmation of the absolute path, accepts only SQLite files inside the
current working directory, checks the extension and SQLite header, rejects symbolic links, and
reapplies migrations after removing the database and its journals. It deletes all data in that file.
There is no production reset/deployment workflow.

Portable SQLAlchemy UUID, JSON, Numeric, enum checks and UTC timestamp mappings permit PostgreSQL
later. That migration still requires a PostgreSQL driver, backend integration tests and operational
configuration. SQLite is the only backend validated in this task.
See [architecture](docs/architecture.md) for tables, relationships and future module boundaries.

## Validation

```powershell
python -m pytest
python -m ruff check .
python -m ruff format --check .
python -m mypy
python -m lead_engine health
```

Tests migrate temporary SQLite databases and cover domain invariants, identity conflicts,
transaction rollback, relationships, score provenance, schema upgrade/downgrade and admin commands.

Recommended next task: define a versioned, campaign-specific deterministic CommercialScorer V1
using attributed findings and explicit scoring components. Keep human approval before outreach.


## Scout V1: discovery without scraping

ScoutService consumes DiscoveryProvider and UnitOfWorkFactory ports. Providers return untrusted
DiscoveryCandidate DTOs; Scout validates/normalizes them as Company records, uses the existing exact
identity service, enriches absent facts, creates attributed discovery evidence and creates/reuses a
Lead in the selected existing Campaign. Query locality is context, never a substitute for missing
company facts. CSV accepts minimal records; insufficient identity signals remain provisional under
the existing deduplication policy. No business/company rules live in a search adapter.

Available providers:

- `csv`: UTF-8 (optional BOM), header-based manual import.
- `static`: saved JSON search-result/demo candidates. It performs no live search or HTTP calls,
  and does not imply search relevance. Query/industry/locality are recorded context; only the limit
  selects a prefix of the fixture. A later live provider can implement the same port independently.

There is no live public-search adapter in V1. The fixture option provides a deterministic pipeline
without depending on authentication, CAPTCHAs or third-party search-result access.
Both example files use synthetic companies and `.example` URLs; they are not researched leads.

### CSV format

A `name` header is required. Other supported headers are `external_id`, `legal_name`, `website`,
`primary_domain`, `phone`, `email`, `address`, `city`, `region`, `country`, `industry`, `subindustry`,
`instagram_url`, `facebook_url`, `linkedin_url`, `google_maps_url`, `source_url`, `source_title`.
Blank optional values become unknown; unknown columns remain in raw provenance. Duplicate/blank
headers or missing name header fail the run. Missing company names, invalid URLs and malformed
row widths are reported per row. Malformed quoting/encoding stops the provider and marks the run
FAILED while retaining prior committed outcomes. CSV uses commas and quoted values as needed.
Physical starting/ending line numbers support quoted multiline cells; empty lines are skipped.
The default import limit is 1000 (maximum 10000), and candidates beyond the limit are not processed.

```powershell
python -m lead_engine db init
python -m lead_engine campaign add --name "Salud Chiclayo" --type HEALTH --geography Chiclayo
python -m lead_engine scout import-csv --campaign "Salud Chiclayo" --file examples/health_chiclayo.csv --report discovery-report.csv
python -m lead_engine scout run --campaign "Salud Chiclayo" --provider static --fixture examples/search_results.json --query "clinicas oftalmologicas" --city Chiclayo --country Peru --limit 20
python -m lead_engine scout runs
python -m lead_engine scout report RUN_UUID --file another-report.csv
```

Campaign accepts UUID or exact name; ambiguous names require a UUID. Each scout command supports
`--database-url`/`LEAD_ENGINE_DATABASE_URL`. `runs` emits JSON records including summary counts.
Report export writes a new UTF-8 CSV with one outcome per received candidate, row number, run/source,
company/lead IDs, flags and reason. It refuses to overwrite files or the import input, and escapes
formula-like cells for spreadsheet readers. Export failures do not undo already committed discovery;
use `scout report` to export the stored run later.

### Provenance, conflicts and transactions

Each received candidate has a Source, including rejected/conflicting/failed candidates. Metadata
records run ID, provider, candidate number, raw input and namespaced provider metadata. CSV metadata
retains filename, absolute file path and physical row numbers; fixture metadata identifies its file
and synthetic nature. Valid source URLs/titles and retrieval time are retained. Invalid provenance
URLs remain in raw metadata on a fallback Source instead of bypassing source validation.

Successful candidates receive DISCOVERY evidence with company/source relationships. Its confidence
of 1 means the candidate was supplied by that source; it does **not** verify the supplied commercial
facts. No website absence/weakness, score or decision-maker conclusion is inferred.
Repeated observations create separate sources/evidence while reusing the company/lead.

Known conflicting facts cause a CONFLICT outcome instead of silent overwrite or partial enrichment.
Case/accent variants of names/localities and equivalent website hosts/paths are recognized; differing
known facts are retained for review. Failed business writes roll back company changes, identities,
source, evidence and lead together; the rejection/error audit commits separately. Good earlier rows
remain committed. Identity/lead uniqueness constraints also protect concurrent inserts; a race is
reported as a conflict and a later run can reuse the committed company/lead.

DiscoveryRun is persisted in `discovery_runs` with campaign, provider, query, timestamps, status and
JSON outcomes. Counts derive from the outcomes rather than duplicated counters. Each candidate's
business data and accepted audit outcome commit together. Runs are RUNNING, COMPLETED, PARTIAL
(rejections/conflicts/errors), or FAILED (interrupted provider/persistence). Abrupt process termination
can leave RUNNING with the last committed outcomes; automatic resume is not implemented.

CLI exit codes: 0 completed, 2 completed with conflicts/rejections, 1 failure or transaction errors.
The sample intentionally returns 2: five candidates, three companies created, one reused, one rejected,
three leads created and one reused. Reimport creates no new companies/leads for this sample.


## Website Auditor V1

WebsiteAuditService depends on HttpFetcher, HtmlAnalyzer and UnitOfWorkFactory ports. HTTPX and
BeautifulSoup implementations live in infrastructure. The auditor inspects only the company's
configured website/landing URL (treated as its homepage), plus robots.txt and bounded HTTP redirects.
It never follows discovered internal/product/service/social/booking links. No JavaScript, browser,
Core Web Vitals, Lighthouse, commercial score, severity or subjective design evaluation is produced.
Signals reflect the supplied static markup, not verified functionality, visibility after CSS/JS or
conversion performance. Broken internal links are not tested in homepage-only V1.

```powershell
python -m lead_engine db init
python -m lead_engine audit website --company-id COMPANY_UUID
python -m lead_engine audit website --domain example.com --force
python -m lead_engine audit campaign --campaign "Salud Chiclayo" --limit 5
python -m lead_engine audit list
```

Specify one company UUID or an already-stored domain; these commands never create a company.
`--database-url`/`LEAD_ENGINE_DATABASE_URL` apply as usual. JSON results include the audit, findings,
reachability, evidence count, errors, warnings and freshness reuse. Exit codes: 0 SUCCESS/NO_WEBSITE
or fresh reuse, 2 PARTIAL, 1 FAILED/persistence errors. A company with no stored website receives
NO_WEBSITE, a manual Source and evidence describing that record state; it is not a business judgment.
Campaign mode processes sequentially, deduplicates company IDs and skips records without websites.

### Findings

Deterministic observations cover successful HTTP reachability/status, HTTPS scheme, redirects,
page title, meta description, h1 count/multiple h1s, viewport, tel/mailto/WhatsApp links, contact-form
markup, ES/EN booking/quote actions, product/catalog/menu/service/privacy links, cart/checkout/purchase
keywords, exact social platform hosts, map links and recognized reservation-provider hosts (OpenTable,
SevenRooms, CoverManager, Resy, Calendly and Booksy). Link signals do not assert that target pages work.
Contact forms require a textarea and a contact/email/tel signal; search-only forms are excluded.
Known script/template/hidden markup is excluded. No external social or booking page is requested.

Address elements are recorded directly; fallback street-name/number and labelled business-hour
patterns carry confidence 0.7. Keyword/form/link rules carry confidence 0.9. Direct structural and
HTTP observations use confidence 1. Derived contact/reservation/quote/product/service/conversion
path findings cite their component finding types. Absence is never labelled a weakness.

### Fetch and access policy

AuditSettings centralizes these defaults: 10-second HTTP operation timeout, 30-second fetch budget,
1,000,000 response bytes, at most 3 homepage redirects, one connect-timeout retry, a one-second
minimum interval between request starts, a 3000 ms slow-response threshold, 7-day freshness and
campaign limit 5 (maximum 100). Slow response is the final page GET elapsed time including body
reading; it is not a browser performance score. Failed requests may have no timing. Body size counts
read bytes. The overall budget is checked between requests/chunks; OS DNS resolution and an in-flight
socket operation are subject to their own timeouts and may extend wall-clock duration beyond it.

The honest User-Agent is `CommercialLeadEngine/0.1` with the repository URL. TLS certificates are
verified. Environment proxies, credentials and cookies are not forwarded. HTTP errors/TLS failures/
read failures are not retried; only an anonymous GET connect-timeout gets one paced retry. HTTP
401/403/429 and static login/password/challenge pages are recorded as blocked access.

Before each homepage origin, robots.txt is retrieved with the same public-address guards and a
100 KB cap. 404/410 means no published policy. Robots errors/access pages fail closed. Disallow is
respected; longer crawl-delay/request-rate directives defer the audit rather than ignoring them.
No robots override or access-bypass CLI flag exists; --force only bypasses freshness.

Streaming enforces declared and actual body caps. Accept-Encoding requests identity; unsolicited
compressed responses are refused before decompression to avoid expansion attacks. Non-HTML content
is PARTIAL without commercial markup inference. Invalid/malformed HTML is parsed conservatively;
no HTML elements or parsing failure produce warnings and PARTIAL.

### SSRF and freshness

Only HTTP(S), ports 80/443, without embedded credentials, are accepted. Every request and redirect
resolves all DNS answers and rejects non-global/private, loopback, link-local, reserved or multicast
addresses, localhost/internal names, cloud metadata destinations and IPv6 transition/mapped ranges.
Mixed public/private answers are rejected. The actual transport connects to a validated literal IP,
retaining the original Host and TLS SNI/certificate hostname. It revalidates DNS at request time to
prevent validation/connection rebinding. Connections are not reused across hostnames sharing an IP.
Tests inject MockTransport and a deterministic resolver; the production CLI has no SSRF bypass.

Only SUCCESS for the same company and configured website within AuditSettings.freshness_days is
fresh. Fresh reuse creates no new evidence. `--freshness-days 0` disables reuse; `--force` requests a
new audit while retaining historical records. PARTIAL/FAILED/NO_WEBSITE are not successful cache hits.
Campaign limits bound newly attempted audits; fresh skips do not consume the budget.

### Persistence and provenance

Migration `0003` adds website_audits and nullable evidence.website_audit_id with foreign keys/indexes.
Every audit references its company and Source. Every finding is Evidence linked directly to that
audit and the same company/source. Source URL is the configured/attempted URL; metadata records
requested/final URLs, HTTP status, final response timing, body size, redirects, scope, error code and
SHA-256 of the decoded HTML text when present. Title and timestamps provide observation context.
Full HTML is not stored; evidence values retain matched snippets/links/counts and explainable rules.
Network access occurs outside the database transaction; Source, final audit and all Evidence commit
atomically or roll back together. A persistence failure is reported, not mislabelled as a site finding.


Offline acceptance is reproducible with `python examples/audit_fixture_demo.py` and documented in
[website audit acceptance](docs/website-audit-acceptance.md). It uses MockTransport fixtures, produces
4 audits/48 attributed evidence records, and removes its temporary database after verification.


## Commercial Scorer V1

CommercialScoringService runs immutable `health-v1`, `construction-v1` and `hospitality-v1` policies.
Each has exactly 100 maximum points, explicit business/opportunity rules, evidence references,
confidence/age eligibility and deterministic explanations. The score ranks commercial opportunity;
it is not website quality or a sales probability. Missing evidence is UNKNOWN, explicit false is
ABSENT, unresolved contradictions are UNCERTAIN. No website alone earns no opportunity points without
a supported commercial anchor. Scores/bands/completeness retain history and never change lead status.

Successful new HTML audits also record a typed tested-signal coverage map; older audits lacking that
map do not turn missing positive findings into false negatives. A new forced audit or an explicit
manual observation can supply those facts. No new migration is needed: existing score/component tables
and JSON explanation metadata store policy version, bands, completeness and observation cutoff.

```powershell
python -m lead_engine evidence add --company-id COMPANY_UUID --type GOOGLE_REVIEW_COUNT --value 427 --value-type integer --source manual
python -m lead_engine score lead --lead-id LEAD_UUID
python -m lead_engine score campaign --campaign "Salud Chiclayo" --min-score 70
python -m lead_engine score explain --lead-id LEAD_UUID
python -m lead_engine score history --lead-id LEAD_UUID
python examples/scoring_fixture_demo.py
```

See [scoring](docs/scoring.md) for weights, exact thresholds, confidence/age windows, conflict behavior,
structured decision-maker evidence, bands, readiness and full CLI examples. The offline four-lead
acceptance produces Health 91, Construction 97, Hospitality 96 and sparse control 0; rescoring keeps
both history records. No scraping, AI inference, subjective design assessment or outreach is added.
