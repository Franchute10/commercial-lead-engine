# LEAD-DISC-001 acceptance record

Executed locally on 2026-10-05 with a temporary SQLite database and temporary CSV reports,
then removed the temporary files. No live network discovery was performed.

1. Applied migrations with `python -m lead_engine db init`.
2. Created campaign `Salud Chiclayo`, type HEALTH, geography Chiclayo.
3. Imported `examples/health_chiclayo.csv` with `scout import-csv` and a new report path.
4. Repeated the same import with a second report path.
5. Ran `db status` and inspected persisted companies, leads, sources, evidence and runs.

| Result | First import | Reimport |
| --- | ---: | ---: |
| Candidates | 5 | 5 |
| Companies created | 3 | 0 |
| Companies reused | 1 | 4 |
| Conflicts | 0 | 0 |
| Rejected | 1 | 1 |
| Errors | 0 | 0 |
| Leads created | 3 | 0 |
| Leads reused | 1 | 4 |

Both imports completed with PARTIAL status and exit code 2, reflecting the intentionally invalid
row at physical line 6: `canonical_name: String should have at least 1 character`.
The fixture contains a clinic with website, a clinic without website, a duplicate, an incomplete
valid clinic, and a missing-name row. All names/URLs are synthetic.

Final database contents: 3 companies, 3 leads, 10 sources, 8 discovery evidence records and 2 runs.
Each received candidate, including both rejected observations, had a source with its run ID and
physical row number. Run outcomes retained source references; row numbers were 2, 3, 4, 5 and 6.
No duplicate leads or company overwrites occurred. Both CSV reports were produced successfully.
`db status` returned exit code 0 with `revision=0002 head=0002`.
