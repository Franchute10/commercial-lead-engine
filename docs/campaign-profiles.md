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

The existing `campaign add --name ... --type ...` CLI remains unchanged. CLI options for profile
and structured targeting are deferred to a follow-up task; campaigns created through the existing
command receive their canonical profile automatically.
