# Salud Chiclayo — Pilot 01: operational runbook

This pilot compares the existing engine with Frank's commercial judgment using real companies.
It does not add discovery, change scoring policies, create a CRM, send outreach or require an
LLM/API key. Preparation includes a header-only CSV template; no real campaign has been executed
and no real companies are supplied by this task. Synthetic test fixtures are not pilot results.

## Scope and what to review

Start with a manageable manually collected cohort (for example 10–20 health companies in Chiclayo).
Include varied service/digital maturity, rather than only companies that look likely to score well.
Frank should record his initial judgment in local notes before reading the engine's ordering where
practical; then compare ranking, cited opportunities, supported decision makers and draft quality.
This is an exploratory convenience sample, not a representative estimate of the whole market.

Company identity, official website, Chiclayo geography and every imported public channel must be
checked manually. Missing information remains unknown. A blank website does not prove no website;
a corporate LinkedIn URL does not identify a person, and a phone does not prove WhatsApp availability.
Retain URLs, observation dates and exact supporting statements. Do not use static demo discovery
or synthetic fixture scripts to populate this real campaign. No LinkedIn scraping/invitations/messages.

## 1. Local environment, isolated database and campaign

From the repository root in PowerShell, use the existing environment. If none exists, follow the
README setup (`python -m venv .venv`, then `.venv\Scripts\python -m pip install -e ".[dev]"`).

```powershell
.\.venv\Scripts\Activate.ps1
$Campaign = "Salud Chiclayo — Pilot 01"
$PilotDir = "pilot_local/pilot01"
New-Item -ItemType Directory -Path $PilotDir -Force | Out-Null
$env:LEAD_ENGINE_DATABASE_URL = "sqlite+pysqlite:///pilot_local/pilot01/pilot.db"
python -m lead_engine health
python -m lead_engine db init
python -m lead_engine db status
python -m lead_engine campaign list
```

`health` tests ephemeral SQLite; `db status` verifies this persisted pilot database. The new
pilot schema head is **0008**. Every CLI respects `LEAD_ENGINE_DATABASE_URL`; keep it set for all
steps, or pass the same `--database-url` explicitly. Existing data is preserved by the migration.

Create the campaign **once**, if the list above does not already contain it:

```powershell
python -m lead_engine campaign add --name "$Campaign" --type HEALTH --geography "Chiclayo, Lambayeque, Peru"
```

Keep the returned campaign UUID. On subsequent sessions reuse that UUID/name; do not add another
campaign with the same name. Names must identify exactly one campaign; UUIDs resolve ambiguity.
All subsequent commands use the same name including the em dash, or can take its saved UUID.

`pilot_local/` is ignored by Git. Keep real company inputs, evaluations, notes and reports there;
only the empty public template and source/docs/tests are committed. Do not run `db reset` on pilot data.

## 2. Collect and import actual companies

```powershell
$Csv = "$PilotDir/companies.csv"
if (-not (Test-Path -LiteralPath $Csv)) {
    Copy-Item -LiteralPath "examples/pilot_companies_template.csv" -Destination $Csv
}
```

Edit `companies.csv` with **real verified entries**; the distributed template contains only headers.
Use UTF-8 (a BOM is accepted), normal CSV quoting and one company per row. Columns:

- `name`: required display name; `legal_name` and `primary_domain` help exact identity.
- `website`: official HTTP(S) URL if known. Verify shared/group domains before importing separate
  clinics: a shared primary domain can represent the same company. Do not invent branch identities.
- `city`, `region`, `country`, `industry`, `subindustry`: explicit public metadata; for this cohort
  record verified Chiclayo / Lambayeque / Peru and the actual health business classification.
- `phone`, `email`, address and social/Maps URLs: only actually published company details.
- `source_url`, `source_title`: actual page/directory that supports the entry, not a made-up URL.

Scout retains import filename, row number, raw cells, source and retrieval timestamp. It deduplicates
stable identities and flags conflicts rather than silently merging uncertain companies. Unknown or
unsupported cells stay blank. The template is not a list of companies and should not be imported empty.

```powershell
$Rows = @(Import-Csv -LiteralPath $Csv)
if ($Rows.Count -eq 0) { throw "Fill the template with verified real companies first." }
$Round = Get-Date -Format "yyyyMMdd-HHmmss"
$ImportReport = "$PilotDir/import-$Round.csv"
python -m lead_engine scout import-csv --campaign "$Campaign" --file "$Csv" --country Peru --limit 100 --report "$ImportReport"
```

Check the exit code before continuing: 0 completed; 2 partial (review rejected/conflicting rows);
1 failed/errors. Do not assume every input row was accepted. Keep the import report and resolve
conflicts manually. Re-imports reuse exact companies/leads and create a new discovery audit trail.
Exports do not overwrite existing paths; use a fresh `$Round` each new pass.

```powershell
$Accepted = @(Import-Csv -LiteralPath $ImportReport | Where-Object { $_.status -eq "ACCEPTED" })
$Accepted | Select-Object name, company_id, lead_id, source_id, status
python -m lead_engine company list
python -m lead_engine scout runs
```

The import report provides the company and lead UUIDs needed below. A company UUID is not a lead UUID.
No new lead-listing or discovery capability is needed. Ensure all intended accepted rows fit the
100-company operational batch limit; larger pilots require deliberate batching, not blind reruns.

## 3. Website audits

```powershell
python -m lead_engine audit campaign --campaign "$Campaign" --limit 100
python -m lead_engine audit list
```

Audits are bounded public homepage inspections with existing robots/HTTP safeguards. They record
errors and reuse fresh successful audits (default seven days). Exit 1/2 requires inspecting failed
or partial audits; it is not evidence that the company has a poor journey. Campaign audits skip
companies without a stored URL. Research those URLs manually. For a verified URL or an explicitly
reviewed no-website record, the existing single-company command is:

```powershell
# Replace this value with a company UUID from the accepted import report.
$CompanyId = "ACTUAL_COMPANY_UUID"
python -m lead_engine audit website --company-id "$CompanyId"
```

Do not treat no configured URL as a verified public absence. If a homepage hides a booking flow,
manually inspect its actual services/contact/booking pages before asserting absence. An audit's
scope does not prove full-site functionality, accessibility, measured conversion or lost revenue.
Use `--force` only when deliberately refreshing a changed website; new audits require rescoring
and regenerating briefs before the next shortlist.

## 4. Manual commercial evidence where necessary

For each company, inspect the actual public source and choose only supported signals. Missing
facts are unknown, not false. Useful HEALTH observations may include specialist/high-value services,
multiple locations, review count/rating, active social presence and the actual appointment path.
Classification such as HIGH_VALUE_SERVICE must have a written justification; do not assume it for
every clinic or infer revenue from treatment names.

The following are operational **parameter templates**, not assertions about any real company.
Set the variables from the observation being recorded, then execute the relevant command:

```powershell
$CompanyId = "ACTUAL_COMPANY_UUID"
$SourceUrl = "ACTUAL_PUBLIC_SOURCE_URL"
$Statement = "ACTUAL_OBSERVATION_AND_SCOPE"
$ObservedAt = "ACTUAL_ISO8601_OBSERVATION_TIME_WITH_OFFSET"
$Signal = "HIGH_VALUE_SERVICE" # Choose the supported signal actually observed.
$BooleanValue = "true" # Or false, only when explicitly established.
python -m lead_engine evidence add --company-id "$CompanyId" --type "$Signal" --value "$BooleanValue" --value-type boolean --source-url "$SourceUrl" --statement "$Statement" --observed-at "$ObservedAt"
```

For an actual published review count/rating, replace the variables with the observed values:

```powershell
$ReviewCount = "ACTUAL_NONNEGATIVE_INTEGER"
$Rating = "ACTUAL_RATING_BETWEEN_0_AND_5"
python -m lead_engine evidence add --company-id "$CompanyId" --type GOOGLE_REVIEW_COUNT --value "$ReviewCount" --value-type integer --source-url "$SourceUrl" --statement "$Statement" --observed-at "$ObservedAt"
python -m lead_engine evidence add --company-id "$CompanyId" --type GOOGLE_RATING --value "$Rating" --value-type decimal --source-url "$SourceUrl" --statement "$Statement" --observed-at "$ObservedAt"
```

A manually checked reservation/contact path can use `HAS_RESERVATION_PATH` with an explicit boolean
and stated inspection scope. Do not assert an absent booking path merely because a CTA was not found
on the homepage. Use separate source/statement/time variables for different facts. See scoring.md
for the supported vocabulary and fixed confidence/freshness rules. Never turn Frank's pilot labels
into `Evidence`, `COMMERCIAL_NOTE` or interaction outcomes; feedback belongs exclusively to `pilot`.

## 5. Score and research people

```powershell
python -m lead_engine score campaign --campaign "$Campaign"
python -m lead_engine score list --campaign "$Campaign"
python -m lead_engine contact research-campaign --campaign "$Campaign" --limit 100
python -m lead_engine contact recommend --campaign "$Campaign" --limit 100
```

No minimum-score filter is used here: inspect the whole small imported cohort and avoid hiding
low-score data gaps. `score campaign --min-score` only filters displayed results; it does not change
which scores are recorded. Contact research uses bounded public company pages, not live search or
LinkedIn automation. Missing people produce target roles rather than invented names.

For manual public-role research use the existing command, replacing every placeholder with a real
person/source/role-company attribution. Add `--linkedin-url`, `--public-email` or `--public-phone`
only if the actual personal/business channel is published and appropriately attributed:

```powershell
$CompanyId = "ACTUAL_COMPANY_UUID"
$PersonName = "ACTUAL_PUBLIC_PERSON_NAME"
$RoleTitle = "ACTUAL_PUBLIC_ROLE_TITLE"
$RoleCategory = "GENERAL_MANAGEMENT" # Select the supported category, not assumed authority.
$RoleSourceUrl = "ACTUAL_PUBLIC_ROLE_SOURCE_URL"
$Association = "ACTUAL_STATEMENT_TYING_PERSON_ROLE_AND_COMPANY"
python -m lead_engine contact add --company-id "$CompanyId" --name "$PersonName" --role "$RoleTitle" --role-category "$RoleCategory" --source-type WEBSITE --source-url "$RoleSourceUrl" --association-statement "$Association"
```

Choose the actual source type (e.g. LINKEDIN_PUBLIC for a manually read public LinkedIn page), not
WEBSITE for every observation. A published corporate inbox does not prove a decision maker's
personal email. Do not guess emails, phone numbers, referrals or purchasing authority. If an
explicit business WhatsApp/form channel is found, use the documented `contact channel-add` command
in contact-research.md; a phone number alone must not be converted to WhatsApp.

If new commercial evidence, audit results or explicit decision-maker-access evidence were added,
rescore. Run the final score pass **after** completing factual enrichment:

```powershell
python -m lead_engine score campaign --campaign "$Campaign"
python -m lead_engine research campaign --campaign "$Campaign" --limit 100 --export "$PilotDir/briefs-$Round.json"
python -m lead_engine research list --campaign "$Campaign" --format csv
```

Briefs identify evidenced opportunities or NO_CLEAR_OPPORTUNITY and show missing data. READY does
not prove real commercial value; it is the engine's deterministic completeness/readiness judgment.
PARTIAL/insufficient data remains research-first under the existing shortlist policy. Any factual
change after this point needs a fresh score/brief/shortlist, with a new evaluation cohort.

## 6. Freeze the shortlist and review its ordering

```powershell
$ShortlistPath = "$PilotDir/shortlist-$Round.json"
python -m lead_engine shortlist today --campaign "$Campaign" --limit 20 --format json --output "$ShortlistPath"
$Run = @(Get-Content -Raw -LiteralPath $ShortlistPath | ConvertFrom-Json)[0]
$RunId = $Run.id
$Run.items | Select-Object shortlist_rank, company_name, lead_id, latest_score, shortlist_priority_score, recommended_next_action
```

Keep this `$RunId` for every evaluation/report in this round. Selected rows can require additional
research or identifying a person; selection alone is not permission to contact. Review `suppressed`
reasons and the import cohort too, recording notable omitted companies in local operator notes.
No recall/false-negative metric is claimed because non-shortlisted companies lack these labels.
Use `shortlist explain --lead-id ACTUAL_LEAD_UUID` for a current explanation; the frozen JSON is the
actual evaluated historical ordering. Its archived score, brief, contact and source references do
not change when later enrichment or regeneration happens.

## 7. Prepare drafts for actionable frozen items

Generate by lead from the frozen shortlist; do **not** call `outreach shortlist` for this round.
That existing command writes another broader shortlist run, which would change the default cohort.
The writer rechecks current eligibility/cooldowns before preparing each draft. Nothing is sent:

```powershell
$DraftActions = @("SEND_LINKEDIN_CONNECTION", "SEND_LINKEDIN_MESSAGE", "SEND_EMAIL", "SEND_WHATSAPP", "MAKE_PHONE_CALL", "PREPARE_CONTACT_FORM", "FOLLOW_UP")
foreach ($Item in $Run.items) {
    if ($Item.recommended_next_action -in $DraftActions) {
        $DraftPath = "$PilotDir/draft-$($Item.lead_id)-$Round.json"
        python -m lead_engine outreach draft --lead-id "$($Item.lead_id)" --language es --format json --output "$DraftPath"
        if ($LASTEXITCODE -ne 0) { Write-Warning "Review draft eligibility for $($Item.lead_id); do not bypass it." }
    }
}
python -m lead_engine outreach list --status DRAFT
```

No draft is created for RESEARCH_MORE, FIND_DECISION_MAKER, NO_ACTION, suppressed leads or internal
manual-review/meeting preparation. A human may explicitly select another supported channel using
`outreach draft --channel EMAIL`; it still must match eligibility and published evidence. Evaluate
the actual saved draft UUID, not a regenerated text. An unmatched lead/brief/recommended person is
rejected by pilot evaluation, rather than silently conflating different outputs.

A missing draft is a validation gap to describe in notes (usually PARTIAL or WRONG depending on
Frank's judgment); do not invent a draft to fill the rubric. Decision-maker UNKNOWN is available.
Outreach validation is not approval to send. Do not run `approve` or `mark-used` merely because a
pilot label says GOOD or YES. Any later external outreach still requires explicit human approval.

## 8. Mark Frank's human evaluation

For **every selected lead**, Frank chooses all labels after inspecting the company, role attribution,
brief and actual draft when available. Do not bulk-assign optimistic defaults. Suggested rubric:

| Label | Meaning |
| --- | --- |
| would_contact YES / NO / MAYBE | Frank would contact / would not / needs clarification |
| decision_maker GOOD / PARTIAL / WRONG / UNKNOWN | Appropriate person / incomplete fit/evidence / incorrect recommendation / no reliable person |
| opportunity GOOD / PARTIAL / WRONG | Relevant supported angle / needs refinement or evidence / unsupported or commercially wrong |
| outreach GOOD / PARTIAL / WRONG | Usable after human review / revise or incomplete / inappropriate or unsupported |

Set the real lead UUID and reviewed draft UUID from the saved files. The choices below illustrate
CLI syntax, **not default labels or findings for real companies**:

```powershell
$LeadId = "ACTUAL_SELECTED_LEAD_UUID"
$DraftId = "ACTUAL_REVIEWED_DRAFT_UUID"
python -m lead_engine pilot evaluate --lead-id "$LeadId" --shortlist-run-id "$RunId" --draft-id "$DraftId" --would-contact YES --decision-maker GOOD --opportunity GOOD --outreach PARTIAL --notes "Frank's actual reason for this judgment" --evaluator Frank
python -m lead_engine pilot history --lead-id "$LeadId"
```

Omit `--draft-id` only when no draft exists or deliberately accepting the automatic latest compatible
draft selection. The returned JSON shows exactly which draft was captured (or null). There is no
synthetic placeholder person, draft or output. Suppressed/unselected leads cannot be recorded as
shortlisted evaluations. Without `--shortlist-run-id`, evaluate/report selects the newest matching
non-future campaign run; passing it explicitly prevents accidental cohort drift.

`PilotEvaluation` stores immutable labels, optional notes, evaluator, aware `evaluated_at` (UTC),
lead/company/campaign/shortlist UUIDs, original score/brief/recommended-contact UUIDs and optional
draft UUID. `pilot-evaluation-v1` and a per-lead/run/evaluator revision preserve every correction.
Re-evaluating appends a new record; reports use the highest revision for each lead in the chosen
run and evaluator. Concurrent duplicate revisions are rejected atomically. Labels never feed
scoring rules, Evidence, pipeline, shortlist priority or draft approval/use.

## 9. Campaign summary and exports

```powershell
python -m lead_engine pilot report --campaign "$Campaign" --shortlist-run-id "$RunId" --evaluator Frank
python -m lead_engine pilot report --campaign "$Campaign" --shortlist-run-id "$RunId" --format csv --output "$PilotDir/evaluation-$Round.csv"
python -m lead_engine pilot report --campaign "$Campaign" --shortlist-run-id "$RunId" --format json --output "$PilotDir/evaluation-$Round.json"
python -m lead_engine pilot report --campaign "$Campaign" --shortlist-run-id "$RunId" --format markdown --output "$PilotDir/evaluation-$Round.md"
```

The report is scoped to **one frozen shortlist and one evaluator**, default Frank, not an average
across multiple dates/cohorts/evaluators. JSON retains the frozen selected items and latest label
records; CSV includes summary counts/distributions on each selected lead row, including unevaluated
rows (or one summary row for an empty shortlist). Markdown includes the readable summary, rubric
counts, artifact IDs and notes. Outputs are UTF-8, use exclusive creation and escape CSV formulas /
untrusted Markdown. Use fresh output names for subsequent exports; no file is overwritten.

| Report value | Exact definition |
| --- | --- |
| Companies evaluated | Unique selected companies with a label record for this run/evaluator |
| Shortlist precision | Contact-agreement proxy: YES / (YES + NO), percent; MAYBE excluded |
| % Frank would contact | YES / all evaluated companies, percent; MAYBE remains in denominator |
| Quality distributions | Counts of the latest decision-maker/opportunity/outreach labels |
| False positives | IDs of evaluated selected leads labeled would_contact=NO; disagreement, not lost sales |
| Needs more research | Selected RESEARCH_MORE/FIND_DECISION_MAKER/REVIEW_MANUALLY, or human MAYBE, non-GOOD decision maker, or non-GOOD opportunity |
| Unevaluated | Selected IDs without labels; not NO, not GOOD and excluded from both percentages |

No decided labels means precision `N/A` (JSON null / CSV empty), not zero. No evaluations means
both percentages N/A. An all-MAYBE cohort has undefined precision and 0% YES. Copy-only outreach
revisions do not automatically imply more factual research. These are transparent counts/ratios,
not accuracy, recall, predicted revenue, response rate or scientific performance claims. NO for a
research-preparation item reflects Frank's contact judgment; it is not proof the engine recommended
sending that item. No scoring weights are changed in this task.

## 10. Finish the pilot round

Verify all selected leads have a human evaluation or document why a review is pending. Discuss the
false positives, missing people/evidence, role fit, opportunity relevance and draft revisions with
Frank using exact source/artifact IDs. Keep raw inputs, shortlist JSON, reviewed draft JSON and
exports together under the round name. Improvements remain written recommendations for a separate
explicitly scoped task; do not “learn” new rules automatically or tune scores during this round.
The readiness checks were offline; real-company validity and commercial agreement are still to be
measured by actually running this pilot. No real-world result is claimed before human labels exist.
