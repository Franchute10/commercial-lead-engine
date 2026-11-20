# Public intent evaluation: measure before expanding

This is an experimental search-index workflow, not a saleable lead feed or an
integration with Facebook/Nextdoor accounts. All three lanes query Tavily.
A `qualified_candidate` is a machine-screened candidate, not a verified buyer.
Dates supplied by an index and contactability still require human review.

## What changed

Every `intent discover` now creates an exclusive JSONL journal under
`pilot_local/intent/` (ignored by Git), even when no candidate survives. An optional
`--run-file` sets a new output path; existing files are never overwritten.
It records run parameters, every attempted query, rank, full returned snippet,
provider date, parsed date, geography/service/request checks and every outcome,
including in-run and database duplicates. Partial failures preserve earlier
observations and reports identify an incomplete/uncommitted run. Authentication
headers, credentials and HTTP bodies from errors are not recorded.

The default is the baseline arm A. `--include-published-date` enables arm B using
Tavily's documented parameter, while retaining the existing queries and regex
filters. RFC 2822 dates returned by Tavily are now parsed as well as ISO/relative
dates. Unknown/future dates stay unknown. No automatic retry/fallback billable
calls were added. Geographic scoring now matches the most specific campaign
scope: matching only US cannot qualify a Sarasota request.

Tavily reference (checked 2026-11-19):
https://docs.tavily.com/documentation/api-reference/endpoint/search
`include_published_date` is opt-in for general search. Requesting it does not
promise every page has a date. `time_range` alone is not proof of recency.

## Windows PowerShell

Use the existing environment/campaign; keep your Tavily key in the local process.
After updating the code, run `.\.venv\Scripts\python.exe -m lead_engine db init`
to apply migration 0014. It retains existing signals and requires their campaign
relationship. If legacy rows have no campaign, it stops with an explanation;
assign the correct campaign before retrying, rather than deleting evidence.
Each command below makes nine basic searches, within the scope shown. Check the
account's remaining free allowance first; no new paid provider is required.

```powershell
.\.venv\Scripts\python.exe -m lead_engine intent discover --campaign "Cleaning1126" --service "house cleaning" --segment "homeowners" --country US --language en --freshness pw --per-query 10 --zone "Bradenton" --zone "Lakewood Ranch" --run-file "pilot_local/intent/day01-A.jsonl"
.\.venv\Scripts\python.exe -m lead_engine intent discover --campaign "Cleaning1126" --service "house cleaning" --segment "homeowners" --country US --language en --freshness pw --per-query 10 --zone "Bradenton" --zone "Lakewood Ranch" --include-published-date --run-file "pilot_local/intent/day01-B.jsonl"
.\.venv\Scripts\python.exe -m lead_engine intent report --run-file "pilot_local/intent/day01-B.jsonl"
```

Use new filenames for subsequent days. Hold all query parameters constant between
arms. Website terms can be added to both arms using the existing website options,
but freeze the reviewed terms/website during comparison. The index may change
between requests, so this is an operational comparison, not a causal experiment.
The default freshness gate in capture remains 30 days; `pw` is a query hint.

## Human review and manual reference set

Open original public URLs and classify each candidate plus a sample of rejected
results. Never infer missing publication dates or private contact details.
Labels: REAL_REQUEST, NOT_REQUEST, PROVIDER, JOB, WRONG_AREA, STALE, UNCLEAR.
REAL_REQUEST means a relevant request in this campaign/time scope; it is not
proof the requester still needs service, will respond, or has consented to outreach.

```powershell
.\.venv\Scripts\python.exe -m lead_engine intent label --run-file "pilot_local/intent/day01-B.jsonl" --url "https://example.org/replace-with-observed-url" --label REAL_REQUEST --note "Observed date and geography; reply path needs manual review"
```

Replace that example with an actual URL observed in the run. Corrections append a
new label; all earlier labels remain. The report uses the most recent per URL.
No outreach is performed. Store only evidence you are allowed to retain; snippets
may contain personal information. The journal is local research data, not a
public artifact. Delete the pilot files when the experiment/retention period ends.

For recall, create a local JSON array of URLs manually observed in the **same
campaign, places and time window**, then pass `--truth-file` to `intent report`.
This measures recall on that sample, not on the whole internet. URL matching strips
fragments/trailing slashes only; aliases/redirects may need manual alignment.

Report definitions:

- Results count includes every hit, including duplicates; unique coverage uses URLs.
- Candidate URLs passed filters, including records already captured in the database.
  This avoids treating the second experiment arm as a failure just due to dedup.
- Precision uses only labelled candidate URLs excluding UNCLEAR. Unlabelled coverage
  is reported alongside it; selective labelling can bias the estimate.
- Retrieval recall = manual URLs found / manual reference URLs.
- Candidate recall = manual URLs passing filters / manual reference URLs.
- Date/geo coverage = unique returned URLs with parseable dates / matching text.
  These describe the index text, not independently verified facts.
- Unmeasured rates and contactable leads are null, not made-up zero/success values.

## Pilot decision

Plan 14 days with a maximum of 252 basic search calls (two nine-query arms/day),
subject to remaining account quota; no scheduler is enabled. Stop earlier after
three comparable runs with no relevant reviewable requests and inspect evidence.
Suggested decision criteria, to be agreed before interpreting results: at least
five manually verified opportunities, candidate precision >=60%, and two marked
useful by the business. Record usefulness/contact outcomes manually in review notes.
Do not sell leads or widen filters to meet a quota. If the source fails, test a
separate B2B-fit source/segment with its own reference set; the broad product goal
is not restricted to household-cleaning requests.

The included tests are offline fixtures. They demonstrate logging and metric
correctness, not live source coverage or demand. A real run needs a valid locally
configured API key; the previously exposed key must be replaced, not reused.
