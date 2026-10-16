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
validated. The engine does not select a profile by proximity.

```text
lead-engine campaign add --name "Clínicas Chiclayo" --profile health-v1 --sector health --subsector private-clinics --country Peru --region Lambayeque --city Chiclayo
lead-engine campaign add --name "Materiales Arequipa" --type CONSTRUCTION --sector construction-real-estate --subsector building-materials --country Peru --city Arequipa
```

## Other / Suggest sector

`SUGGEST_SECTOR` is an intake path, not an official sector and not a business classification. It
represents a request for a category that a human should map and review. The taxonomy does not
classify arbitrary names automatically.

For example, a person may explicitly enter “Agroexportación” and map it provisionally to
`industry-manufacturing` / `agroindustry`. The result preserves the requested name and has
`PROVISIONAL` status, with no recommended profile unless one is explicitly supplied and valid.
Unknown or uncertain suggestions require human evaluation. Suggestions remain domain values only;
they are not persisted.

No scoring, research, shortlist, or `CampaignType` values or policy behavior are changed by this
taxonomy. `CampaignType` remains a compatibility field that constrains existing policy resolution.
A future change may resolve policies solely through `profile_id`, allowing the operational
dependency on `CampaignType` to be retired. Migration `0011` adds nullable classification columns
without backfilling legacy campaigns.
