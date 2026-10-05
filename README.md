# Commercial Lead Engine

Local-first foundation for researching companies with potential website, customer journey,
lead generation, reservation, quotation and sales opportunities. This bootstrap provides
contracts, an application health check and a SQLite connectivity adapter. It does not yet
implement company discovery, scraping, scoring, decision-maker research or outreach.

## Local setup

Python 3.12+ is required. From this directory in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -m lead_engine --help
.\.venv\Scripts\python -m lead_engine health
```

On macOS/Linux use `.venv/bin/python` in place of `.\.venv\Scripts\python`.
With the environment activated, use `python -m lead_engine` or `lead-engine`.
The health command executes `SELECT 1` against an ephemeral SQLite database and returns
exit code 0 on success, 1 on failure. It creates no database files or tables.
No API keys, paid SaaS, Airtable, n8n or OpenAI API are required.

## Architecture

- `domain`: immutable Pydantic company and evidence contracts.
- `application`: use cases and typed repository/connectivity ports.
- `infrastructure`: SQLAlchemy 2 connectivity implementation.
- `cli`: Typer commands and dependency composition.

Evidence retains a source URL and timezone-aware observation time. Company identities must
be resolved upstream; the future repository must enforce uniqueness atomically. No repository
implementation or schema exists yet, so this bootstrap does not claim persistent deduplication.
See [architecture](docs/architecture.md) for responsibilities and future boundaries.

SQLite is the initial backend. SQLAlchemy URLs and provider-independent ports allow PostgreSQL
later; that migration will also require its driver, schema migrations and database integration tests.
Human approval is required before any outreach. Automated LinkedIn scraping, invitations,
messages and automatic email/WhatsApp sending are outside scope.

## Validation

```powershell
.\.venv\Scripts\python -m pytest
.\.venv\Scripts\python -m ruff check .
.\.venv\Scripts\python -m ruff format --check .
.\.venv\Scripts\python -m mypy
```

Recommended next task: add company/source persistence with schema migrations, a unique canonical
identity constraint, explicit duplicate handling and round-trip provenance tests. Keep discovery
behind a source port when it is introduced.
