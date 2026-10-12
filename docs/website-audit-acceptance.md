# LEAD-WEB-001 acceptance record

Offline acceptance uses the real HTTPX adapter with an injected MockTransport and deterministic
public DNS answers. There are no actual network connections and no production security override.
The mock receives IP-pinned requests with original Host/TLS SNI. The CLI's production fetcher always
uses the public-address policy. HTML files are synthetic fixtures under tests/fixtures/website.

Reproduce from the repository with the virtual environment activated:

```powershell
python examples/audit_fixture_demo.py
```

| Company fixture | Audit status | Required finding | Evidence persisted |
| --- | --- | --- | ---: |
| booking.html | SUCCESS | BOOKING_CTA_PRESENT | 19 |
| contact.html | SUCCESS | WHATSAPP_LINK_PRESENT | 15 |
| catalog.html | SUCCESS | CATALOG_PRESENT | 10 |
| Company without a stored URL | NO_WEBSITE | NO_WEBSITE | 1 |

The demo verifies persistence before cleaning its temporary SQLite database: 4 audits, 4 Sources
and 45 Evidence records. Each finding references its audit and the same company/source. Three
configured pages require exactly 6 mocked requests (robots + homepage). The no-website record
requires no fetch. Neither internal links nor social/booking/catalog destinations are requested.

Additional automated acceptance covers CLI company/domain/campaign/list commands, freshness and
force, HTTP errors/timeouts, private redirects, DNS rebinding guards, robots restrictions, response
limits and transaction rollback. Migration tests upgrade populated 0002 data to 0003 and preserve
historical evidence and score associations during upgrade/downgrade.

No live public-site performance or availability is claimed by this fixture acceptance.


LEAD-SCORE-001 adds one explicit tested-signal coverage Evidence record to each successful HTML audit.
The historical LEAD-WEB-001 table above remains the original acceptance record. Re-running the current
demo now yields 20/16/11/1 evidence respectively (48 total), with the same 4 audits/4 Sources and 6
mocked requests. This additive observation lets scoring distinguish measured absence from unknown.

## WEB-AUDIT-002 — Bounded partial fetch resilience

Real-world validation after PILOT-002 showed that some public HTML homepages exceed the configured
response-size budget or time out during body retrieval. These conditions must remain bounded without
turning unobserved page content into negative commercial evidence.

WEB-AUDIT-002 keeps the existing SSRF, DNS rebinding, redirect, robots, compression and response
budget safeguards. It changes only successful public HTML responses:

- `text/html` and `application/xhtml+xml` may retain a body prefix up to `max_response_bytes`.
- If the HTML body exceeds that budget, the retained prefix is returned with an explicit truncation
  warning instead of `RESPONSE_TOO_LARGE`.
- If an HTML read timeout occurs after at least one body chunk was received, the retained prefix is
  returned with an explicit partial-read warning.
- A timeout before any usable body is received remains a hard `TIMEOUT`.
- robots.txt and non-HTML resources retain the prior strict hard-fail behavior.
- Unsupported compressed responses remain rejected.
- Partial HTML audits are `PARTIAL`, not `SUCCESS`.
- Positive findings observed in the retained prefix may be persisted.
- `WEBSITE_SIGNAL_COVERAGE` is never emitted for a partial document, so signals not observed in the
  retained prefix remain unknown rather than false.
- A recoverable partial HTML response does not emit `FETCH_ERROR`.

Automated acceptance covers both oversized HTML and read-timeout-after-prefix behavior, plus the
audit-layer rule that partial content may establish positive findings only.

Real-world validation on an isolated Pilot 02 database produced:

| Prior condition | WEB-AUDIT-002 result |
| --- | --- |
| Oversized public HTML response | PARTIAL / HTTP 200 / truncated prefix retained |
| Oversized public HTML response | PARTIAL / HTTP 200 / truncated prefix retained |
| Timeout before usable body | FAILED / TIMEOUT |

Both oversized HTML cases produced positive metadata findings without `WEBSITE_SIGNAL_COVERAGE`
or `FETCH_ERROR`. The timeout case returned no usable body and therefore correctly remained FAILED
rather than manufacturing a partial observation.

Validation gates:

- 59 focused website-audit tests passed.
- 437 full-suite tests passed.
- Ruff passed.
- mypy passed on 79 source files.
- `git diff --check` passed.

This task does not change scoring weights, research policy, outreach policy, hostname attribution,
template/context interpretation or commercial opportunity rules.
