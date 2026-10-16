# ADR: `profile_id` as canonical commercial identity

- **Status:** Accepted for implementation planning; production code unchanged
- **Decision date:** 2026-10-16
- **Repository baseline:** `2a1b326`
- **Migration head:** `0012`
- **Scope:** Campaign identity, legacy compatibility, policy selection, and the future B2B profile

## Context

Campaign profiles already have stable IDs and versioned scoring, research, and shortlist references.
The commercial policy registry is keyed by profile ID. However, `CampaignType` still acts as both a
historical classification and an active policy discriminator. `Campaign`, `CampaignProfile`, the
scoring and research contracts, policy registry validation, ORM schema, CLI, and some downstream
outputs still require or interpret it. This prevents a fourth profile from being independent of the
three historical verticals.

The sector catalogue is separate data, but its optional `recommended_profile_id` is currently
treated as a mandatory match during campaign validation. That makes a recommendation behave like a
hard classification and prevents an explicitly selected compatible profile where the catalogue has
no recommendation.

## Decision

Adopt strategy B: **`profile_id` is the canonical commercial identity of every executable
Campaign. `CampaignType` remains an optional, historical compatibility attribute.** The enum stays
limited to `HEALTH`, `CONSTRUCTION`, and `HOSPITALITY`; do not add B2B or future verticals to it.

Historical profiles retain their current `CampaignType` associations for compatibility. New
profiles may have no legacy type. Scoring, research, contact fit, shortlist policy, and outreach
content are selected from the validated profile's explicit, versioned policy references. A sector,
subsector, industry label, or suggestion never creates or selects a policy implicitly.

The profile registry remains closed and strict: a known explicit profile is required for execution;
an unknown explicit profile fails. There is no default-to-HEALTH or other catch-all behavior.

## Alternatives considered

### A. Add `B2B` to `CampaignType`

This preserves the current shape of campaign data, but keeps policy identity tied to an enum. Every
new vertical or specialized B2B profile would require another enum value and database constraint
change. A single `B2B` value also cannot distinguish future logistics, manufacturing, distribution,
or services profiles without adding profile-specific exceptions beside the enum. This option is
rejected because it repeats the coupling this ADR is intended to remove.

### B. Canonical profile ID; nullable legacy `CampaignType` — selected

This gives every campaign one stable policy identity and lets old rows retain their type values.
The initial code and schema refactor is substantial: all active type-based selection and matching
must move to profile references, and the database column must become nullable. Afterward, adding a
profile does not expand `CampaignType`. This is the selected option because it makes the domain
model express the intended identity rather than because it is the smallest patch.

### C. Separate persisted compatibility mapping

A compatibility table could map historical enum values to profile IDs while new campaigns use a
profile ID without a legacy type. This still requires optional `campaign_type` or another way to
represent new campaigns, and adds migration, joins, integrity rules, and an administration surface
for only three fixed historical mappings. A code-level, unique compatibility adapter is sufficient
for the current fixed legacy enum. Revisit a persisted mapping only if legacy aliases must be
managed independently of releases.

## Architectural invariants

1. `profile_id` is the sole canonical commercial identity and policy lookup key.
2. Every executable campaign resolves to exactly one known profile. New writes must provide a known
   profile ID; a legacy type-only input may pass through the compatibility adapter, which resolves
   the historical profile before campaign creation.
3. Missing profile and missing legacy type is an error. Missing type never means HEALTH or any
   other implicit profile.
4. A missing profile with one of the three known historical types can resolve through the legacy
   adapter. Resolution is pure: reading or scoring a legacy object must not mutate it or write an
   inferred value back to persistence.
5. When an explicit profile ID is supplied, it is authoritative. An unknown explicit ID fails even
   if a valid historical type is also present. If both are present, the type must equal the
   profile's historical association; a profile with no legacy association cannot be paired with a
   historical type.
6. Each legacy `CampaignType` maps to exactly one historical profile. The resolver rejects
   duplicate or missing mappings at registry construction/startup.
7. Policy references on a profile resolve to registered, versioned implementations. Registry
   validation compares the policy reference/version to the selected implementation; it does not
   require a matching `CampaignType`.
8. Existing policy versions, weights, scoring behavior, band thresholds, and historical score
   records remain unchanged by the identity refactor.
9. `campaign_type` is never populated with a fabricated value for a new profile. Serialized output
   represents its absence as JSON `null` (or omits it only in an explicitly versioned output
   contract); it never emits `B2B` or another sentinel.
10. Sector and subsector are classifications, not executable policy selectors. Suggestions remain
    review data and never register profiles or policies.
11. Human approval remains mandatory before outreach use. Profile selection must not enable sending
    email, WhatsApp, or social messages automatically.
12. Unknown profile IDs, unresolved sector compatibility, and missing required evidence fail
    closed; they do not fall back to a historical policy.

## Domain and sector contracts

### Campaign and CampaignProfile

`Campaign.profile_id` is required and validated for new domain writes. `Campaign.campaign_type`
becomes `CampaignType | None`. `CampaignProfile.campaign_type` becomes optional and means only
“the historical type associated with this legacy profile”; it is not the profile's identity. The
three existing profiles keep their current associations. `b2b-general-v1` has no association.

The compatibility resolver follows this order:

1. If `profile_id` is supplied, resolve it strictly. If a type is also supplied, validate the
   historical association; never replace an invalid or conflicting explicit ID.
2. If `profile_id` is absent and a known legacy type is supplied, resolve the unique historical
   profile in memory. New CLI/API creation through this compatibility path persists the resolved
   canonical profile ID and preserves the supplied type.
3. If neither is supplied, fail.

A legacy database row with a null profile may be read through a non-mutating effective-profile
resolver only when its historical type resolves unambiguously. It must not be rewritten merely by
loading, scoring, or researching it. Migration 0010 has already backfilled existing rows; any
remaining null row should be reported for review.

### Sector and subsector consistency

Sector classification remains independent from profile identity. Define a maintained compatibility
relation between sector IDs and allowed profile IDs, separate from the sector catalogue's
`recommended_profile_id`:

- A recommendation is a **default suggestion** when the user does not provide a profile.
- An explicit profile must be known and must be listed as compatible with the selected sector.
- If an explicit profile differs from the recommendation but is listed as compatible, accept it;
  the recommendation does not override explicit user choice.
- If the sector has no recommendation and no explicit profile, fail with a request for an explicit
  profile. Never infer one from the sector name.
- If the sector has no recommendation but an explicit compatible profile is supplied, accept it.
- If the sector/profile pair is absent from the compatibility relation, reject it. A human must
  review and add that compatibility explicitly; suggestions alone do not do so.
- A subsector requires a known parent sector and must belong to it. Unknown sectors/subsectors
  fail.

This separates three concepts that are currently conflated: sector membership, allowed profile
compatibility, and a preferred profile recommendation. Initially, existing recommended sector
mappings can also be the only allowed mapping for those sectors. Add B2B compatibilities only after
explicit review. Do not assign all four target sectors to B2B merely because they are in scope.

## Policy registry and downstream contracts

Profiles select versioned policy references. The existing bundle registry is the implementation
anchor, but remove its type-equality checks and bind by the profile's policy references. `Campaign`
is resolved once to a known profile before any policy runs.

| Capability | Required behavior after the refactor |
|---|---|
| Campaign add | Accept a known `--profile` without `--type`. Keep historical `--type`-only input as an adapter to the corresponding old profile. Reject unknown profiles and conflicts. A selected sector uses the compatibility rules above. |
| Campaign list | Serialize `profile_id` and nullable `campaign_type`; never display a made-up type. |
| Campaign profiles | Enumerate the profile registry rather than a hard-coded three-ID list; show optional legacy type and policy references. |
| Discovery | Use the resolved profile and structured geography/industry. A profile without defaults requires explicit targeting where the discovery source needs it. Sector labels do not silently select discovery policy. |
| Scoring | Select by the profile's scoring reference. Remove CampaignType-keyed default selection/guards from the canonical path. Keep old scoring output exactly stable for the three existing profiles. |
| Research | Select by the profile's research reference. Rules and generated opportunity types must be versioned, evidence-backed, and valid for that profile. |
| Contact research | Resolve fit and target roles by profile. Retain a type-based helper only as an explicit legacy adapter. A public title or profile URL alone does not establish authority or reachability. |
| Shortlist | Apply the shared or profile-referenced shortlist policy. Keep `--type` as an optional historical filter; add canonical `--profile` filtering. A null type matches no type-specific filter, not an implicit category. Include `profile_id` in output and make legacy type nullable. |
| Outreach | Resolve a closed, versioned template/angle from the profile. Ensure a new profile has safe fallback content and cannot trigger a KeyError or open-ended claims. Drafting remains separate from human approval and use; no send operation is introduced. |

If profile references are designed to share a policy implementation, registry validation should
validate the named policy reference, not require one unique policy object per profile. Outreach
needs a profile-specific template reference or an explicit generic template. The current fixed
`outreach-v1` binding and three-entry angle/subject dictionaries are not sufficient for an
arbitrary fourth profile.

## Compatibility acceptance matrix

| Input/state | Expected result |
|---|---|
| Existing HEALTH campaign with `health-v1` | Load unchanged; preserve `HEALTH` and profile ID; resolve the same policies and outputs. |
| Existing CONSTRUCTION campaign with `construction-v1` | Same preservation and policy behavior. |
| Existing HOSPITALITY campaign with `hospitality-v1` | Same preservation and policy behavior. |
| Historical type-only input `--type HEALTH` | Compatibility adapter resolves `health-v1`; creation stores that profile and preserves `HEALTH`. |
| Explicit matching `health-v1` + `HEALTH` | Accept. |
| Explicit mismatching `health-v1` + `CONSTRUCTION` | Reject; do not fall back or overwrite either input. |
| Unknown explicit profile, with or without a valid old type | Reject. |
| `b2b-general-v1` with no legacy type | Accept when the profile and its policies are registered. Serialize `campaign_type: null`. |
| Sector with recommendation; no explicit profile | Use the recommendation only if it is a known, allowed profile for that sector. |
| Sector with recommendation; explicit compatible profile equal to recommendation | Accept. |
| Sector with recommendation; explicit different but allowed profile | Accept and preserve explicit selection. |
| Sector with recommendation; explicit incompatible profile | Reject. |
| Sector without recommendation plus explicit allowed profile | Accept. |
| Sector without recommendation and without explicit profile | Reject; no fallback. |
| Subsector without sector | Reject. |
| Unknown sector or subsector | Reject. |
| Historical shortlist filter `--type HEALTH` | Return only campaigns whose nullable legacy type is `HEALTH`; do not reinterpret null as any type. |
| Canonical shortlist filter `--profile b2b-general-v1` | Return campaigns with that exact validated profile, independent of legacy type. |

## Migration 0013 contract

Migration 0013 makes `campaigns.campaign_type` nullable. It must not rewrite historical rows or
modify migration 0010. Existing IDs, `CampaignType` values, profile IDs, foreign keys from `leads`,
lead rows, and stored scores remain intact. New B2B rows store `profile_id = 'b2b-general-v1'` and
`campaign_type = NULL`.

SQLite migration requirements:

1. Use Alembic's SQLite batch/table-rebuild mechanism for the nullable change; do not hand-edit or
   rerun an already-applied migration.
2. Preserve the enum check for non-null values (`HEALTH`, `CONSTRUCTION`, `HOSPITALITY`). SQLite
   permits `NULL` through a CHECK expression; verify this behavior on the project's supported
   SQLite version.
3. Preserve the Campaign primary key, timestamps, all campaign fields, constraints, indexes, and
   dependent Lead foreign keys. Do not recreate or delete dependent rows.
4. Run upgrade and downgrade tests against a newly created, isolated SQLite file. Capture campaign,
   lead, and score identifiers/counts before and after. Run `PRAGMA foreign_key_check` after the
   rebuild and exercise repository round-trips.
5. Verify old campaign types and profile IDs byte-for-byte/value-for-value; verify a null legacy
   type with an explicit B2B profile can be inserted and read.
6. Do not point migration tests or CLI smoke tests at the user's local/main database.

**Downgrade:** before restoring `NOT NULL`, check for any `campaign_type IS NULL`. If any exist,
abort with a clear error and leave the database unchanged. Do not assign a fabricated type or
silently delete B2B campaigns. If none exist, restore the old constraint safely. Document that a
database containing B2B campaigns cannot downgrade to a schema that requires a historical type.

**Database invariants:** a non-null `campaign_type` must be one of the three historical enum
values; IDs and referential integrity are preserved. **Domain invariants:** every executable
campaign resolves to a known profile; a new persisted campaign has a profile ID; optional legacy
type is either one of the old values or null; when both explicit profile and type are present they
must match; a null type never selects a profile by itself.

## Risks and controls

| Risk | Control |
|---|---|
| Existing score or research output changes during type decoupling | Freeze exact score components, totals, versions, and representative research outputs for all three historical profiles before implementation; compare after every relevant task. |
| A forgotten CampaignType branch remains in a consumer | Search all source and CLI code for type-based lookup/conditionals; classify each as compatibility, filter/output, or policy selection; tests must exercise every canonical downstream path with `campaign_type=None`. |
| SQLite rebuild damages Lead relationships | Isolated migration fixture with IDs/counts, foreign-key enforcement, `PRAGMA foreign_key_check`, and repository read/write tests. |
| Sector recommendation is mistaken for compatibility | Keep an explicit allowed-profile relation; test recommendation/default separately from compatibility and explicit choice. |
| Weak public evidence is treated as an opportunity | Require an attributable positive commercial anchor, scope-complete gap evidence, and citations; retain unknown/incomplete status. |
| B2B general profile overfits its first sector | Freeze a common evidence core; pilot one subsector; examine disagreements by business model before generalizing or creating a specialized profile. |
| Public job titles imply decision authority | Keep role/title association separate from verified authority and reachability; use explicit evidence and conservative role fit. |
| CRM, sales volume, margin, or intent is inferred from a website | Treat these as unknown unless directly supported by an attributable public source; do not infer a sales-process defect from a missing CRM mention. |
| Outreach renderer lacks safe B2B language | Require a versioned closed template/angle; test every channel and ensure drafts remain pending human approval. |
| Downgrade loses campaigns with null type | Make downgrade stop before schema alteration whenever null types exist; never invent legacy values. |

## Implementation roadmap

Tasks are independently reviewable. Keep each task uncommitted until its own acceptance suite passes;
do not include real campaign data or credentials in fixtures.

| Task | Prerequisites | Acceptance criteria | Migration effects | Main regression risks |
|---|---|---|---|---|
| 1. Campaign/profile domain compatibility | This ADR approved; current resolver and all type call sites inventoried. | Optional legacy type, profile-only construction, strict unknown/conflict handling, pure legacy resolution, sector compatibility rules and matrix tests. Missing type never defaults. | None. | Pydantic validation changes; accidental inferred-profile mutation; historical field mismatch. |
| 2. Migration 0013 and ORM | Task 1 defines null semantics; an isolated SQLite migration harness is ready. | Nullable enum column; existing IDs/types/profiles/leads/scores unchanged; null-type profile round-trip; FK check clean; safe blocking downgrade tested. | Adds 0013; no edits to 0010–0012. | SQLite table rebuild, constraint loss, foreign-key disruption, downgrade data loss. |
| 3. Commercial policy contract decoupling | Task 1 and policy reference semantics agreed. | Registry selects by profile references only; unknown references fail; scoring and research contracts no longer require a legacy type; all three historic golden outcomes unchanged. | None. | Stale policy-map key, changed scoring or research versions, hidden CampaignType branches. |
| 4. CLI and downstream compatibility | Tasks 1–3; nullable serialization shape agreed. | Profile-only campaign add; registry-backed profiles list; list emits null type; discovery, scoring, research, contact, shortlist and outreach handle null type; historical `--type` filter remains exact; canonical profile filter works. | None beyond 0013. | Output/API compatibility, null handling, invalid sector fallback, outreach template failure. |
| 5. B2B signal and opportunity vocabulary | Evidence requirements and target pilot segment agreed. | Typed, attributable signal definitions; unknown distinct from false; opportunity taxonomy and minimum citation/coverage requirements; role categories or mappings documented. | None unless new persisted fields are explicitly justified. | Unobservable metrics, vague labels, overcollection, false absence claims. |
| 6. B2B scoring and research policies | Task 5; rubric frozen as a versioned hypothesis. | `b2b-general-v1` policy references resolve independently; rubric maximum is 100; per-component tests prove evidence gates; research rules require relevant anchors and cited gaps. No adjustments to legacy profiles. | None. | Applying one sector's rules across all B2B; missing evidence inflates scores; duplicate or uncited opportunities. |
| 7. B2B contact/outreach integration | Task 5; role and template review completed. | Role fit is profile-driven; role evidence and decision authority remain separate; outreach has closed B2B variants, is evidence-grounded, and remains human-approved. No automated sends. | None. | Wrong decision-maker ranking; unsupported copy; unintended outbound side effect. |
| 8. Controlled pilot | Tasks 1–7 verified; roster and evaluation protocol pre-registered. | 20–30 real, source-attributed companies in one sector/subsector; frozen policy; all companies human-evaluated; report raw counts and disagreements; no automatic policy tuning. | No product migration. Pilot records use existing campaign/evaluation schema unless a separate approved gap is found. | Small/nonrepresentative sample; evaluator bias; ranking reviewed only for shortlist items; silent mid-pilot changes. |

## Pilot protocol

Use **industry/manufacturing → plastics** as the proposed first controlled sample, in one selected
geography (for example Lima/Callao), subject to a preflight confirming 20–30 qualifying real
companies can be documented from public sources. Narrowing by subsector reduces variation in
commercial offers while testing B2B product discovery, specifications, quotation paths, and
decision-maker research. This sample cannot validate logistics, distribution, or B2B services; those
need later pilots or a deliberately stratified follow-up.

1. Define inclusion/exclusion criteria and the target geography before collecting the roster. Use
   public company, association, registry, and official website sources; record source URL, retrieval
   date, and identity evidence. Do not create placeholder/fake companies to reach the sample size.
2. If fewer than 20 qualified companies can be sourced, pause and approve a new segment or geography
   before scoring. Do not silently widen the segment.
3. Freeze the B2B scoring and research versions, evidence coverage, shortlist settings, and human
   review protocol before the run. No tuning against the same sample.
4. Inspect equivalent website scope for every company. Record inaccessible pages and incomplete
   coverage. Add manual commercial evidence only with provenance. Do not convert “not found in this
   inspection” into a general fact of absence.
5. Run scoring, contact research, commercial briefs, shortlist, and human-approved outreach drafts.
   Do not send messages automatically.
6. Have an expert assess all 20–30 companies, including those outside the shortlist, against the
   same predeclared rubric. Where practical, the evaluator first records contact/opportunity
   judgments without seeing the engine's score or rank; a second reviewer resolves material
   disagreements.
7. Report raw counts: human `YES`/`MAYBE`/`NO` by shortlist position, shortlisted `NO` cases,
   human-`YES` leads outside the shortlist, decision-maker/opportunity/outreach quality labels,
   and evidence insufficiency. Do not present a small convenience sample as general model accuracy.
8. Review disagreements by score component and business model. Keep feedback observational; any
   policy change requires a separate versioned decision and validation set.

Before launch, the pilot owner must set acceptable operating thresholds (such as tolerated
shortlisted `NO` count and minimum high-priority human-`YES` coverage) without inspecting the
resulting rankings. These thresholds are campaign decisions, not universal statistical claims.

## Approval boundary

This ADR approves the canonical identity and compatibility architecture for implementation
planning. It does **not** approve B2B scoring weights, create profile or sector mappings, authorize a
production migration, populate the pilot roster, or change outreach policy. Those remain separate
reviewable tasks with their own evidence, tests, and approval.
