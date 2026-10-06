# Decision Maker Finder V1

The finder answers who is relevant and which public observations support that recommendation.
Contact fit is independent of commercial LeadScore. No contact, score or research result changes
pipeline state or proves purchasing authority. The finder never emits DECISION_MAKER_ACCESS or
automatically awards commercial lead-score points. Human review and outreach remain mandatory.
No AI, paid API, inferred ownership, generated addresses or phone numbers are used.

## Providers and transactions

`DecisionMakerResearchService` consumes `ContactDiscoveryProvider` and the existing repository/UoW
ports. Domain contracts and policies import no infrastructure. Providers finish network work before
one atomic transaction inserts contacts, identities, Sources, Evidence and a research run. Invalid
identity/company associations roll back the entire batch. Provider I/O failures produce FAILED runs
without contact claims; per-page restrictions produce PARTIAL runs with attributable URL warnings.

- ManualContactProvider: explicit human observations; CLI requires source URL and association statement.
- CompanyWebsiteContactProvider: homepage plus at most two directly linked team/about/contact pages
  (configurable 1–5 total). Exact same hostname, one link level, no query/fragment crawl, normal HTTP.
  Production uses the SSRF-safe HTTPX adapter with same-domain redirects, DNS/IP pinning, robots,
  pacing, timeout, response and redirect limits. Robots redirects are guarded too. Login, CAPTCHA,
  authentication failures and non-HTML responses are skipped. LinkedIn URLs are manual-only.
- PublicSearchProvider: typed future port. StaticPublicSearchProvider serves explicit fixtures only;
  no Google scraping or live search integration is included. A search snippet alone is UNVERIFIED.

Static HTML extraction accepts explicit ES/EN role/name lines in leaf cards, such as
`Gerente General: Ana Pérez`, `Director Médico: Dr. Juan García`, and
`Marketing Manager – María López`. JSON-LD Person requires matching worksFor.name and explicit
jobTitle. Hidden content is excluded. Unrecognized layouts return no person rather than a guess.
Public email/phone in JSON-LD and manually supplied channels are retained exactly; no inferred
company-address patterns or phone enrichment is performed. LinkedIn/profile URLs are stored,
never fetched. The initial website parser intentionally has limited layout coverage.

## Provenance and states

Each candidate creates its own Source with URL, title, retrieval time and metadata. CONTACT_ROLE,
CONTACT_COMPANY_ASSOCIATION and explicit CONTACT_PUBLIC_PROFILE/EMAIL/PHONE observations retain the
complete typed candidate snapshot and contact UUID in Evidence, with observed_at and confidence.
An explicit context assertion creates CONTACT_COMPANY_CONTEXT, citing the same source. This is an
observation history, not an overwrite of the original Contact fields. Use recommend for current roles;
contact list exposes the original persisted contact record.

SUPPORTED requires a person name, explicit role, matching company ID/name, association statement,
confidence >=0.7, non-future observation and non-search source. Manual statements are human
attestations to what the cited source says; URL presence does not verify their truth. UNVERIFIED
candidates remain stored but cannot enter recommendations. VERIFIED is reserved for a future explicit
verification workflow and never assigned automatically. STALE applies after the configurable default
30-day observation window; records remain stored and recommendations warn to reconfirm. Same-date
supported observations with different role titles/categories are CONFLICTED and lose 20 fit points.
Older different roles remain visible as history warnings; the latest supported role is used.
Observation freshness is when information was seen, not proof that employment remains current.

## Identity and conflicts

Within a company, prefer normalized LinkedIn/public profile host+path (fragment/query and trailing
slash ignored), then case-folded public email, then normalized exact name plus role category.
Name normalization removes accents and casing, not fuzzy similarity. Identical names across companies
remain separate company-contact associations. A shared profile/email with a different name,
disagreeing identity keys or a new contradictory public identity requires manual review and rolls
back. Without a shared strong identity, incompatible roles remain separate candidates; they are not
silently merged. Unique database identity keys protect concurrent inserts. Legacy contacts with exact
compatible name/category can be reused. Role/category snapshots preserve all later observations.

## Source reliability and confidence

Reliability contributes 0–10 points:

| Source | Points |
|---|---:|
| Exact official company website hostname | 10 |
| Government/business registry | 9 |
| Public LinkedIn/professional observation | 8 |
| Public Facebook/Instagram observation | 7 |
| Directory or manually attributed external URL | 6 |
| Other source | 5 |
| Search snippet | 2 |

Official hostname takes precedence even for manual input. Source types are provider/human assertions;
V1 does not authenticate ownership of social accounts. Unknown websites receive 5 rather than
claiming they are official. A valid manual URL is not automatically low quality, but a URL alone
cannot establish a role. HIGH confidence requires reliability >=8, candidate confidence >=0.85,
fresh and non-conflicted evidence. Other supported contacts are MEDIUM. Contact confidence is
separate from the source score and from lead scoring.

## Fit policies (maximum 100)

Role relevance contributes at most 50, supported association 20, recency 10, source reliability 10,
and an explicitly observed personal channel 10. Without a personal public channel, contactability
contributes zero. No shared company inbox or general phone is attributed to a person.

| Role | Health | Construction | Hospitality |
|---|---:|---:|---:|
| Owner / Founder | 50 | 50 | 45 |
| General Management | 46 | 46 | 42 |
| Marketing / Brand | 44 | 40 | 46 |
| Digital / Ecommerce | 44 | 38 | 40 |
| Medical Director | 42 | 10 | 10 |
| Commercial / Business Development | 36 | 44 | 35 |
| Customer Experience | 10 | 10 | 40 |
| Administration / Operations | 25 | 25 | 25 |
| Unknown | 10 | 10 | 10 |

Existing categories are retained: explicit Brand titles map to MARKETING, Ecommerce to DIGITAL,
Operations to ADMINISTRATION and Business Development to COMMERCIAL; original job titles remain
in evidence. No enum rewrite or destructive Contact table migration is needed.

Only sourced context changes the weights. `independent` makes hospitality owner/founder 50;
`group` makes marketing/digital 50 and caps explicit restaurant-manager titles at 25;
`small_clinic` makes medical director 50. Context must include a cited context_statement; no size
classification is inferred from company name or industry. The manual provider supplies these
assertions; the V1 static parser does not guess company context.

Recommendations sort by descending fit, normalized name and contact UUID; explanations expose all
five components and evidence UUIDs. No supported contact yields a valid no-person result with
Marketing, General Management and Digital role targets. No names or channels are manufactured.

## CLI

Apply migration 0004 before use:

```powershell
python -m lead_engine db init
python -m lead_engine contact add --company-id COMPANY_UUID --name "Ana Pérez" --role "Marketing Manager" --role-category MARKETING --source-url "https://company.example/team" --association-statement "Company team page names Ana Pérez as Marketing Manager" --confidence 0.95
python -m lead_engine contact list --company-id COMPANY_UUID
python -m lead_engine contact research --lead-id LEAD_UUID
python -m lead_engine contact research --lead-id LEAD_UUID --force --freshness-days 30
python -m lead_engine contact recommend --lead-id LEAD_UUID --limit 3
python -m lead_engine contact recommend --campaign "Hospitality Lima" --limit 10
python -m lead_engine contact research-campaign --campaign "Salud Chiclayo" --min-lead-score 70 --limit 5
python -m lead_engine contact runs
python examples/contact_fixture_demo.py
```

Manual add supports source-type, LinkedIn/profile URL, public-email, public-phone, and paired
context/context-statement options. These describe explicitly observed public information.
All database commands support --database-url / LEAD_ENGINE_DATABASE_URL. Campaign resolution uses
exact name or UUID; ambiguous names fail. Recommendation limit applies to leads and per-lead contacts
in campaign mode. Batch processing is sequential, defaults to 5 leads, uses the latest LeadScore for
threshold filtering, and reuses successful company/provider runs within the freshness window.
Missing scores never qualify for a supplied threshold. Force bypasses freshness only, not HTTP guards.

## Offline acceptance

Five fixtures produce Health General Manager 86 ahead of Medical Director 82, Construction Owner 90
ahead of Commercial Manager 84, independent restaurant Owner 90 ahead of Operations Manager 65,
and restaurant-group Brand Manager 90 ahead of Restaurant Manager 65. The no-person company returns
role targets. Each named result references persisted Source-backed Evidence. Run the demo without
network access; it uses an ephemeral migrated SQLite database. PostgreSQL-compatible mappings and
an additive migration are included; live PostgreSQL validation remains future work.

No LinkedIn scraping, invitations, messaging, email sending, WhatsApp sending, auth/CAPTCHA bypass,
JavaScript/browser execution or automated outreach exists in this module.
