# Economic sector taxonomy

The sector catalog is a small, stable business vocabulary. It is independent from campaign profiles
and does not determine commercial scoring, research, or shortlist policies.

## Sector, subsector, and campaign profile

A **sector** is a broad economic category, such as `logistics-transport` (Logística y transporte).
A **subsector** is an optional, narrower category within a sector, such as
`logistics-operators` (Operadores logísticos). A **campaign profile** is a versioned, validated
bundle of existing commercial policies, such as `health-v1`.

These concepts can exist independently. For example:

- Sector: Logística y transporte (`logistics-transport`)
- Subsector: Operadores logísticos (`logistics-operators`)
- Profile: none yet

This means the taxonomy recognizes the business category, but no commercial policy has been
validated for it. The same applies to industry/manufacturing, commerce/distribution, education,
B2B services, and technology/digital services. No profile is assigned to those sectors by
proximity or guesswork.

Currently validated recommendations are `health` → `health-v1`, `construction-real-estate` →
`construction-v1`, and `hospitality-tourism` → `hospitality-v1`. A sector can still be used in the
catalog when its recommended profile is `null`.

## Official catalog

The nine official sectors are returned in stable display order by:

```text
lead-engine campaign sectors
```

Each output line is a JSON sector record. To list a sector's optional subsectors:

```text
lead-engine campaign subsectors --sector health
```

Unknown sector IDs produce an error. The catalog is read-only, while a Campaign may persist an
explicit `sector_id` and optional `subsector_id`. Existing campaigns keep both values null; no
classification is inferred from profile, industry, subindustry, geography, or campaign name.
Selected IDs must exist in the catalog, and the selected subsector must belong to the sector.

Explicit sector classification is currently executable only for `health`,
`construction-real-estate`, and `hospitality-tourism`, whose recommended profiles have been
validated. The other six remain catalogued but Campaign creation rejects them until a profile is
validated. When one of the three supported sectors is supplied without `--profile` or `--type`, the
CLI derives its recommended profile and corresponding legacy campaign type. Explicit profile or type
values are accepted only when they agree with that sector. The engine does not select a profile by
proximity.

```text
lead-engine campaign add --name "Clínicas Chiclayo" --sector health --subsector private-clinics --country Peru --region Lambayeque --city Chiclayo
lead-engine campaign add --name "Materiales Arequipa" --sector construction-real-estate --subsector building-materials --country Peru --city Arequipa
```

## Other / Suggest sector

`SUGGEST_SECTOR` is an intake path, not an official sector and not a business classification. It
represents a request for a category that a human should map and review. The taxonomy does not
classify arbitrary names automatically.

An **official economic sector** is one of the fixed catalog entries above. A **suggested economic
activity** is user-submitted intake and may remain unmapped while pending review. A **human-approved
classification** is a suggestion explicitly accepted with a valid existing sector; acceptance does
not create an official sector. A **validated executable CampaignProfile** is a separately maintained
bundle of policies that campaigns may use. Accepting a suggestion does not create a profile or make
the suggestion executable.

Suggestions are persisted independently from Campaign. The requested name is retained verbatim;
proposed sector, subsector, and profile IDs are validated when supplied, but no profile is inferred
from a sector. No human action here mutates the official sector catalog or creates a campaign.

```text
lead-engine sector-suggestion submit --name "Seguridad electrónica"
lead-engine sector-suggestion submit --name "Agroexportación" --sector industry-manufacturing --subsector agroindustry
lead-engine sector-suggestion list
lead-engine sector-suggestion show --id 00000000-0000-0000-0000-000000000000
lead-engine sector-suggestion review --id 00000000-0000-0000-0000-000000000000 --sector b2b-services --note "Clasificación revisada"
lead-engine sector-suggestion accept --id 00000000-0000-0000-0000-000000000000 --note "Clasificación aceptada"
lead-engine sector-suggestion reject --id 00000000-0000-0000-0000-000000000000 --note "No corresponde a una categoría diferenciada"
```

Only a human review action can move a suggestion out of `PROVISIONAL`. `NEEDS_REVIEW` remains
pending until accepted or rejected. Acceptance requires a valid existing sector mapping; rejection
requires a nonblank note. `ACCEPTED` and `REJECTED` are terminal. `reviewed_at` is populated only
for those terminal decisions. Migration `0012` adds the independent `sector_suggestions` table.

No scoring, research, shortlist, or `CampaignType` values or policy behavior are changed by this
taxonomy. `CampaignType` remains a compatibility field that constrains existing policy resolution.
A future change may resolve policies solely through `profile_id`, allowing the operational
dependency on `CampaignType` to be retired. Migration `0011` adds nullable classification columns
without backfilling legacy campaigns.
