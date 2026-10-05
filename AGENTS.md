# Repository rules

- Keep this foundation local-first; no paid service or API key is required.
- Domain imports neither application nor infrastructure. Application depends on domain ports.
- Integrate external sources through typed interfaces; preserve source URLs, timestamps and evidence.
- Deduplicate companies by a stable identity before persistence; never silently merge uncertain matches.
- No automated LinkedIn activity or outbound email/WhatsApp. Human approval precedes outreach.
- Use Python 3.12+, typed code, SQLAlchemy 2 and portable database constructs.
- Keep future modules focused on explicit inputs, outputs and evidence. Avoid premature orchestration.
- Validate with `python -m pytest`, `python -m ruff check .`, `python -m ruff format --check .`,
  and `python -m mypy` before completing changes.
- Do not commit secrets, local databases or virtual environments.
