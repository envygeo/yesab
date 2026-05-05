# YESAB Static Map Builders

YESAB is the Yukon Environmental and Socio-economic Assessment Board, which tracks assessment projects across Yukon. This repository exists to pull the published project map data and registry metadata into reproducible local artifacts so it is easier to inspect, rebuild, and share static map outputs without depending on the live services at runtime.

- downloading the YESAB shapefile archive
- caching the YESAB registry API in year buckets
- building static map outputs from the zipped shapefiles in `data/`

## Scripts

- `scripts/download_project_map_archive.py`
  Downloads `all.zip` only when the remote file changed.
- `scripts/refresh_api_cache.py`
  Caches YESAB API project records into local year-bucket Zstandard-compressed JSON files and writes a merged dataset.
- `scripts/build_static_map_single.py`
  Builds a single self-contained HTML file.
- `scripts/build_static_map_split.py`
  Builds a multi-file static site with separate HTML, CSS, JS, and layer data files.
- `scripts/build_geopackage.py`
  Builds an enriched GeoPackage with the same shapefile/API joins and approximate API-only points used by the map builders.
- `scripts/refresh_and_build_geopackage.py`
  Downloads the latest map archive when changed, refreshes the API cache, and builds the GeoPackage in one command.

## Usage

Run commands from the repository root. If you omit output arguments, the builders write safely into `./out` without clobbering each other.

Output arguments differ by builder:

- `scripts/build_static_map_single.py` accepts either an `.html` file path or a directory. Directory output writes `yesab-map-in-one.html` inside that directory.
- `scripts/build_static_map_split.py` accepts an output directory and recreates that directory before writing.
- `scripts/build_geopackage.py` accepts a `.gpkg` file path.

Use `uv` with Python `3.14+`.

```powershell
uv run .\scripts\download_project_map_archive.py

uv run .\scripts\refresh_api_cache.py
uv run .\scripts\refresh_api_cache.py --force
uv run .\scripts\refresh_api_cache.py --start-year 2024 --end-year 2025 --force
uv run .\scripts\refresh_api_cache.py --years 2022 2023 2024 --force

uv run .\scripts\build_static_map_single.py
uv run .\scripts\build_static_map_single.py .\some-output-dir

uv run .\scripts\build_static_map_split.py
uv run .\scripts\build_static_map_split.py .\some-output-dir

uv run .\scripts\build_geopackage.py
uv run .\scripts\build_geopackage.py .\some-output.gpkg

uv run .\scripts\refresh_and_build_geopackage.py
uv run .\scripts\refresh_and_build_geopackage.py .\some-output.gpkg
uv run .\scripts\refresh_and_build_geopackage.py --force
uv run .\scripts\refresh_and_build_geopackage.py --years 2024 2025 --force .\some-output.gpkg
```

## Testing

Run the regression tests with the same Python version the builders require:

```powershell
uv run --python 3.14 python -m unittest discover -s tests
```

For new join, QA, API fallback, or details-panel behavior, add a failing fixture-style test first, then make both builders pass through the shared helper path.

Typical workflow:

1. Refresh the shapefile archive when needed.
2. Refresh the API cache.
3. Rebuild one or both map outputs.

## Output

Default output locations:

- `scripts/download_project_map_archive.py` writes:
  - `data/yesab_all.zip`
  - `data/yesab_all_zip.state.json`
- `scripts/refresh_api_cache.py` writes:
  - `data/api/buckets/projects_<start>-<end>.json.zst`
  - `data/api/projects_merged.json.zst`
  - `data/api/state.json`
- `scripts/build_static_map_single.py` writes:
  - `out/yesab-map-in-one.html`
  - `out/yesab-map-in-one.qa.html`
  - `out/yesab-map-in-one.qa.json`
- `scripts/build_static_map_split.py` writes:
  - `out/yesab-map/index.html`
  - `out/yesab-map/app.css`
  - `out/yesab-map/app.js`
  - `out/yesab-map/data/`
  - `out/yesab-map/qa_report.html`
  - `out/yesab-map/qa_report.json`
- `scripts/build_geopackage.py` writes:
  - `out/yesab-projects.gpkg`
- `scripts/refresh_and_build_geopackage.py` writes the same download, API cache, and GeoPackage outputs as the three scripts it chains.

The split builder removes and recreates only its own target directory before writing files.

## API Cache Behavior

`scripts/refresh_api_cache.py` defaults to refreshing the current year bucket only.

Older cache buckets stay on disk until you explicitly refresh them with `--force`.
This keeps the sync logic simple while still updating the projects most likely to change.

Refresh API cache buckets sequentially. The script uses a shared `data/api/state.json` file and is not designed for concurrent writers.

## Notes

- Python `3.14+` is required for stdlib `compression.zstd` support.
- If `data/api/projects_merged.json.zst` exists, both map builders will enrich matching features with YESAB registry metadata.
- QA reports are generated with both builders so you can inspect map/API coverage and unmatched records.

## Follow-up Items

- Investigate whether the GeoPackage build should consume a shared data-preparation layer directly instead of depending on `scripts/build_static_map_single.py`. The current dependency works and keeps behavior aligned, but the pipeline order is surprising: static-map assembly now acts as the input path for the GIS artifact.

## Metrics Workflow

This repo uses lightweight agent-session, command-run, and decision metrics under `metrics/`.

```powershell
uv run .\scripts\run_timed.py --task-id tests -- uv run --python 3.14 python -m unittest discover -s tests
uv run .\scripts\summarize_metrics.py
```

The reusable starter template now lives in `maphew/agent-templates`.
