# LEAD-SCORE-001 offline acceptance

Reproduce with `python examples/scoring_fixture_demo.py` after local installation.
The demonstration uses synthetic names, .example URLs, explicit manual observations and a
fixture HttpFetcher; no network/search/AI is used. Sources mark their fixture nature.

| Fixture | Policy | Score | Band | Weighted completeness |
| --- | --- | ---: | --- | ---: |
| Strong Health business, weak static conversion paths | health-v1 | 91 | A | 100% |
| Distributor, absent catalog/quotation markup | construction-v1 | 97 | A | 100% |
| Active restaurant, weak reservation markup | hospitality-v1 | 96 | A | 100% |
| Sparse control with no stored website | health-v1 | 0 | E | 0% |

Each maximum totals 100. All three commercial fixtures rank above control. Every component earning
points references attributed evidence, including explicit audit coverage for measured absent signals.
Unknown business facts on the control do not become negative claims; website absence alone earns 0.
Rescoring creates a second score per lead with equal totals and preserves the first record. The demo
asserts two historical records per lead before removing its temporary SQLite database.

Detailed unit/integration tests cover unresolved/recent confidence conflicts, missing/false evidence,
reputation thresholds, verified role/access, prior audits without coverage, bands/versioned profiles,
manual typed values, CLI explain/history/campaign commands and rollback after component insertion.
