# Commercial Lead Engine

Local-first commercial research foundation. The engine models the gap between a company's
commercial potential and its digital/customer acquisition capability. It does not equate
opportunity with absence of a website. Important conclusions are stored as attributed evidence.

The current version provides a typed domain, SQLite persistence, Alembic migrations, application
services, Scout CSV/demo discovery and a manual admin CLI. It performs no scraping, crawling,
external API calls, AI inference,
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

`db init` applies packaged Alembic migrations to head (`0002`) and can be repeated safely.
Revision `0001` creates the core domain; `0002` adds the discovery audit trail. From the repo,
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

Recommended next task: a bounded WebsiteAuditor V1 with explicit public-access rules, observable
findings and source evidence. Keep commercial scoring and automatic outreach out of that task.


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
