# Campaign profiles and targeting

Campaigns persist a stable `profile_id` alongside the legacy `campaign_type`. The profile ID is
derived from the campaign type when omitted and must resolve to the same vertical when supplied.
Scoring, research, and shortlist policies continue to resolve from `campaign_type`.
Migration `0010` backfills existing campaigns using a fixed mapping and preserves their IDs,
leads, and legacy geography. The SQLite column remains nullable to avoid rebuilding the referenced
campaign table; application-created domain campaigns always resolve and require a profile ID.

Structured `country`, `region`, and `city` fields are separate from the legacy free-text `geography`
field. The engine does not parse or infer structured values from `geography`. `industry` and
`subindustry` are targeting data and do not select a policy. Persisted structured fields can be
passed into `discovery_query_for_campaign()` to create a provider-neutral discovery query.

The campaign CLI accepts either the legacy vertical type, a versioned profile, or both when they
match. `--profile` selects the vertical policy bundle; structured targeting narrows the campaign's
intended companies and does not select or alter policies. The legacy `--geography` value remains
free text and is not parsed into country, region, or city.

```text
# Existing type-only command remains supported.
lead-engine campaign add --name "Clinics in Chiclayo" --type HEALTH --geography "Chiclayo, Lambayeque, Peru"

# A profile supplies its campaign type. Structured targeting is stored separately.
lead-engine campaign add --name "Clinics in Chiclayo" --profile health-v1 --country Peru --region Lambayeque --city Chiclayo --industry health --subindustry "private clinics"

# Supplying both is valid only when the type matches the profile.
lead-engine campaign add --name "Builders in Lima" --profile construction-v1 --type CONSTRUCTION --country Peru --city Lima

lead-engine campaign profiles
lead-engine campaign list
```

`campaign profiles` prints the three built-in profiles in stable order: `health-v1`,
`construction-v1`, and `hospitality-v1`. Their policy references describe supported behavior; they
do not imply support for every sector. Unknown profiles and mismatched profile/type pairs fail
before database writes.

Campaign targeting is passed to `discovery_query_for_campaign()` for provider-neutral query
construction. This CLI does not perform discovery.

Campaigns may also store an explicit `sector_id` and optional `subsector_id`, which classify the
economic category independently from discovery targeting. Legacy campaigns keep both fields null;
the migration does not infer a sector from `profile_id`, `industry`, `subindustry`, or `geography`.
At present, a selected sector must recommend the same validated profile as the campaign. Only
`health`, `construction-real-estate`, and `hospitality-tourism` can be used for executable campaigns;
the other six catalog sectors have no validated profile and are rejected for campaign creation. Do
not substitute a different sector's profile; see [the sector taxonomy](sector-taxonomy.md) for the
classification and profile relationship. With a supported `--sector`, `--profile` and `--type` may
both be omitted; the sector resolves the recommended profile and its corresponding campaign type.

```text
lead-engine campaign add --name "Clínicas Chiclayo" --sector health --subsector private-clinics --country Peru --region Lambayeque --city Chiclayo --industry healthcare --subindustry "outpatient clinics"
lead-engine campaign add --name "Materiales Arequipa" --sector construction-real-estate --subsector building-materials --country Peru --city Arequipa
```

`campaign_type` remains the compatibility input for current policy resolution. A future policy
resolution change may depend on `profile_id` alone, allowing that operational dependency to be
retired without inferring sector classifications for old campaigns.
