# Commercial Lead Engine

Local-first commercial research foundation. The engine models the gap between a company's
commercial potential and its digital/customer acquisition capability. It does not equate
opportunity with absence of a website. Important conclusions are stored as attributed evidence.

The current version provides a typed domain, SQLite persistence, Alembic migrations, application
services and a manual admin CLI. It performs no scraping, crawling, external API calls, AI inference,
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

`db init` applies packaged Alembic migration `0001` to head and can be repeated safely. From the repo,
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

Recommended next task: define a provider-independent Scout discovery port and implement a manual
CSV import adapter with source attribution and review of duplicate/conflicting identities.
