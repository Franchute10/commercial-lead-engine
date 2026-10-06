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
