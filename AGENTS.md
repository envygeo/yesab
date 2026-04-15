# AGENTS.md

`README.md` is the human-facing overview. This file only captures agent-relevant operating constraints and repo conventions.

Preference order for accessing or getting supporting tools:
  uvx > npx > uv tool install > pip install > npm install

## Working Rules

- Preserve the current output split:
  - single-file build at `out/yesab-map-in-one.html`
  - split build under `out/yesab-map/`
- `build_static_map_split.py` recreates its target directory. Keep it isolated to `out/yesab-map/` or another dedicated directory.
- Update both builders when changing shared behavior such as joins, styling, details panels, or QA generation.
- Keep the low-complexity API bucket cache model unless there is a clear reason to add a more complex sync design.

## API Cache Constraints

- Cache state is shared in `data/api/state.json`.
- `cache_yesab_api.py` is safe for one writer at a time only. Do not run concurrent refreshes.
- Refresh cache before rebuilding map outputs when working on API-enriched behavior.

## Join Assumptions

- Builders currently match shapefile features to API records by project number.
- Feature properties used for joins:
  - `ProjectID`
  - `Prj_ID`
  - `YESAB_PROJ`
  - `Number`
- API field used for joins:
  - `projectNumber`
- Do not assume complete overlap between shapefile geometry and API project records. Check QA outputs after join changes.

## Generated Artifacts

- Treat `out/`, `data/api/`, `journal/`, and ad hoc probe JSON files as generated or local-working artifacts unless the task says otherwise.
- Do not delete or overwrite unrelated generated artifacts casually.
