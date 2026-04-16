# YESAB Static Map Builders

YESAB is the Yukon Environmental and Socio-economic Assessment Board, which tracks assessment projects across Yukon. This repository exists to pull the published project map data and registry metadata into reproducible local artifacts so it is easier to inspect, rebuild, and share static map outputs without depending on the live services at runtime.

- downloading the YESAB shapefile archive
- caching the YESAB registry API in year buckets
- building static map outputs from the zipped shapefiles in `data/`

## Scripts

- `dnld-yesab-project-map-file.py`
  Downloads `all.zip` only when the remote file changed.
- `cache_yesab_api.py`
  Caches YESAB API project records into local year-bucket JSON files and writes a merged dataset.
- `build_static_map.py`
  Builds a single self-contained HTML file.
- `build_static_map_split.py`
  Builds a multi-file static site with separate HTML, CSS, JS, and layer data files.

## Usage

The builders accept an optional output path. If you omit it, they write safely into `./out` without clobbering each other.

```powershell
python .\dnld-yesab-project-map-file.py

python .\cache_yesab_api.py
python .\cache_yesab_api.py --force
python .\cache_yesab_api.py --start-year 2024 --end-year 2025 --force
python .\cache_yesab_api.py --years 2022 2023 2024 --force

python .\build_static_map.py
python .\build_static_map.py .\some-output-dir

python .\build_static_map_split.py
python .\build_static_map_split.py .\some-output-dir
```

Typical workflow:

1. Refresh the shapefile archive when needed.
2. Refresh the API cache.
3. Rebuild one or both map outputs.

## Output

Default output locations:

- `dnld-yesab-project-map-file.py` writes:
  - `data/yesab_all.zip`
  - `data/yesab_all_zip.state.json`
- `cache_yesab_api.py` writes:
  - `data/api/buckets/projects_<start>-<end>.json`
  - `data/api/projects_merged.json`
  - `data/api/state.json`
- `build_static_map.py` writes:
  - `out/yesab-map-in-one.html`
  - `out/yesab-map-in-one.qa.html`
  - `out/yesab-map-in-one.qa.json`
- `build_static_map_split.py` writes:
  - `out/yesab-map/index.html`
  - `out/yesab-map/app.css`
  - `out/yesab-map/app.js`
  - `out/yesab-map/data/`
  - `out/yesab-map/qa_report.html`
  - `out/yesab-map/qa_report.json`

The split builder removes and recreates only its own target directory before writing files.

## API Cache Behavior

`cache_yesab_api.py` defaults to refreshing only recent "hot" buckets:

- previous two years as one bucket
- current year as one bucket

Older cache buckets stay on disk until you explicitly refresh them with `--force`.
This keeps the sync logic simple while still updating the projects most likely to change.

Refresh API cache buckets sequentially. The script uses a shared `data/api/state.json` file and is not designed for concurrent writers.

## Notes

- If `data/api/projects_merged.json` exists, both map builders will enrich matching features with YESAB registry metadata.
- QA reports are generated with both builders so you can inspect map/API coverage and unmatched records.
