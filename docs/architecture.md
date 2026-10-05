# Architecture

Build an organization with explicit responsibilities and evidence. There is no agent runtime or
orchestration framework. Domain models validate local invariants; application services validate
relationships and identity; infrastructure implements storage; CLI composes dependencies.

```mermaid
flowchart LR
    CLI[Typer composition root] --> APP[Application services / Scout]
    SCOUT[ScoutService] --> PROVIDER[DiscoveryProvider port]
    CSV[CSV adapter] -. implements .-> PROVIDER
    STATIC[Saved search fixture adapter] -. implements .-> PROVIDER
    APP --> PORTS[Repository and UnitOfWork ports]
    APP --> DOMAIN[Domain models and normalization]
    CLI --> INFRA[SQLAlchemy repositories / UnitOfWork]
    INFRA -. implements .-> PORTS
    INFRA --> DB[(SQLite)]
    MIG[Alembic revisions] --> DB
```

## Domain schema

```mermaid
erDiagram
    COMPANY ||--o{ CONTACT : employs
    COMPANY ||--o{ COMPANY_IDENTITY : identified_by
    COMPANY ||--o{ EVIDENCE : supported_by
    SOURCE ||--o{ EVIDENCE : attributes
    COMPANY ||--o{ LEAD : evaluated_as
    CAMPAIGN ||--o{ LEAD : qualifies
    CAMPAIGN ||--o{ DISCOVERY_RUN : targets
    LEAD ||--o{ LEAD_SCORE : scored_with
    LEAD_SCORE ||--|{ SCORE_COMPONENT : explains
    SCORE_COMPONENT ||--o{ COMPONENT_EVIDENCE : cites
    EVIDENCE ||--o{ COMPONENT_EVIDENCE : supports
    LEAD ||--o{ LEAD_INTERACTION : tracks
    CONTACT o|--o{ LEAD_INTERACTION : involves
```

Tables: `companies`, `contacts`, `sources`, `evidence`, `campaigns`, `leads`, `lead_scores`,
`score_components`, `lead_interactions`, plus `company_identities` and `component_evidence`.
Revision `0002` adds `discovery_runs`; Alembic manages `alembic_version`. All entity IDs are UUIDs.
No separate pipeline table exists: `PipelineStage` aliases `LeadStatus` to avoid conflicting states.

## Evidence and qualification

Each evidence record requires a company, source, statement, category, confidence and observation time.
Source retrieval and evidence observation times are distinct. URLs are normalized public HTTP(S)
URLs without credentials/fragments. Manual sources can omit a URL. Source metadata and raw values
are JSON; component evidence references are relational foreign keys. Company/contact creation does
not itself establish a conclusion or designate a decision maker.

Scores are immutable historical aggregates. Decimal points, unique criteria, positive maximums,
maximum total of exactly 100, and sum of awards equal to total make each stored score reproducible.
Version and explanations identify the evaluation context. The service rejects evidence from another
company. Scoring rules and campaign weights are outside this task.

## Identity

`application.identity` exposes signal generation and duplicate resolution. It uses exact normalized
domain, then legal name/locality, then canonical name/locality. Name normalization preserves original
record names while comparing case, accents, punctuation and whitespace consistently. Location needs
city and country. Name keys are SHA-256 of normalized inputs; domain keys retain the normalized host.

`company_identities.key` is unique. Different signals resolving to different companies or a name
match with conflicting domains cause an explicit error. A matching company's known values are
preserved; missing details are enriched. UUID lookup supports already-known provisional records.
Without enough signals, automatic name matching is unsafe and new records remain provisional.
Shared domains/branches and conflicting facts require future human review; no fuzzy merge occurs.

## Persistence boundaries

`LeadService` runs against repository/unit-of-work protocols. Its caller owns an explicit transaction;
SQLAlchemyUnitOfWork flushes writes, commits on request and rolls back on uncommitted exit or failure.
Repositories map records but contain no qualification/scoring policies. SQL constraints supplement
application checks: identity and company/campaign uniqueness, foreign keys, enums and scalar bounds.
Cross-company relationships and cross-row score totals are validated by the application/domain;
writing raw SQL bypasses these guarantees and is not a supported business write path.

Alembic revision `0001` creates the schema from an empty database. CLI init uses the same migrations
as direct Alembic. Tests compare migrated schema to ORM metadata, round-trip records and exercise
upgrade/downgrade. No `create_all` shortcut or production deployment is used.

SQLite connections enable foreign keys. UTC timestamps round-trip safely through SQLite's timezone
limitations. SQLAlchemy mappings support a later PostgreSQL migration, subject to driver setup and
backend tests. Local reset is deliberately restricted and requires confirmation; keep clients closed.

## Lifecycle and future responsibilities

LeadStatus covers DISCOVERED, QUALIFYING, QUALIFIED, REJECTED, READY_FOR_RESEARCH,
READY_FOR_OUTREACH, CONTACTED, RESPONDED, MEETING, PROPOSAL, WON, LOST and ARCHIVED.
This task models states, not transition policy. Interactions track human actions without sending
communications or automatically changing status. READY_FOR_OUTREACH is not proof of human approval;
a future outreach workflow must introduce and enforce its explicit approval gate.

| Capability | Input | Output | Evidence obligation |
| --- | --- | --- | --- |
| Scout V1 (CSV/demo) | Query, campaign, provider | Companies, leads, run audit | Source and observation provenance |
| WebsiteAuditor | Company website | Structured findings | URLs, timestamps, observable signals |
| CommercialScorer | Company and findings | Versioned score/components | Rationale and evidence IDs |
| DecisionMakerFinder | Company and public source ports | Possible people/roles | Public sources and confidence |
| ResearchAnalyst | Findings and contacts | Outreach intelligence | Attributed claims and uncertainty |
| OutreachWriter | Human-approved intelligence | Draft | Claim references and approval record |
| PipelineManager | Human actions and stage changes | Pipeline history | Actor, timestamps and outcomes |

A future shortlist use case selects from scores/pipeline records and retains the evidence explaining
selection. Live web discovery, crawling, external APIs, AI inference, automatic outreach, LinkedIn automation
and final user interfaces remain outside this task. CSV and fixture discovery are now implemented.


## Scout capability

```mermaid
flowchart TD
    Q[Existing campaign and query context] --> P[DiscoveryProvider]
    P --> C[Untrusted candidate DTO]
    C --> V[Company validation and exact identity resolution]
    V --> F{Known facts conflict?}
    F -->|No| W[Enrich company / Source / DISCOVERY evidence / create or reuse lead]
    W --> A[Commit business writes and run outcome atomically]
    F -->|Yes| R[Rollback business writes]
    V -->|Invalid or write failure| R
    R --> E[Commit Source and rejected/conflict/error audit]
    A --> NEXT[Next candidate]
    E --> NEXT
    NEXT --> FINISH[Finalize persisted run and optional CSV report]
```

Scout accepts provider-neutral DiscoveryQuery/DiscoveryCandidate DTOs. Query fields include campaign,
country, locality, industry, text and a bounded limit. All candidate facts may be unknown except a
usable company name at validation time. Context does not invent candidate locality. The CSV adapter
reads UTF-8 headers and preserves original cells/line numbers. StaticDiscoveryProvider consumes local
fixtures; it never fetches HTTP, scrapes search pages or filters by implied search relevance.

Scout uses existing repository and UnitOfWork ports. One transaction handles a candidate's company,
identity keys, lead, source, evidence and accepted audit result. Identity matches enrich missing facts;
known differing facts cause a reported conflict and no partial enrichment. Successful observations
always get new provenance, while companies and company/campaign leads are reused. No score is produced.

DiscoveryRun is a persisted aggregate with JSON query/outcomes and derived counters. Audit JSON
references sources/company/lead IDs; business evidence remains relational with mandatory source FKs.
Outcome updates commit with business writes. Rejection/conflict/error sources and outcomes commit in
a fresh transaction after rollback. Source metadata namespaces untrusted provider metadata below
`provider_metadata` and keeps authoritative run references separate. Invalid provenance URLs are kept
only as raw candidate data in fallback sources. Source/evidence record receipt, not verification.

Migration `0002` adds a campaign-indexed discovery_runs table without rewriting `0001` or its data.
Tests cover empty-database migration, upgrade from populated `0001`, downgrade and ORM equivalence.
Provider failure finishes a FAILED run; abrupt process termination may leave RUNNING for manual
inspection. There is no scheduler/resume/retry orchestration. CLI run/report commands expose the
persisted audit trail and counts; reports never overwrite existing files.

All discovery tests are local fixtures and temporary SQLite. No network client dependency is added.
