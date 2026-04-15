"""Build a single-file static HTML map from zipped shapefiles in ``data/``.

The generated output embeds both the application code and all parsed layer data
into one HTML document so it can be opened directly from disk without a server.
"""

# /// script
# requires-python = ">=3.10"
# ///
from __future__ import annotations

import argparse
import json
import struct
import zipfile
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
API_CACHE_FILE = DATA_DIR / "api" / "projects_merged.json"
API_STATE_FILE = DATA_DIR / "api" / "state.json"
ZIP_STATE_FILE = DATA_DIR / "yesab_all_zip.state.json"
DEFAULT_OUTPUT_PATH = Path("./out/yesab-map-in-one.html")
PROJECT_MAP_PAGE_URL = "https://yesab.ca/project-map"
PROJECT_MAP_ARCHIVE_URL = (
    "https://yesab.ca/wp-content/plugins/yesab-map-wp-plugin/geojson/all.zip"
)
REGISTRY_FRONT_URL = "https://yesabregistry.ca/"
REGISTRY_API_URL = "https://yesabregistry.ca/api/integration/projects"
YST = timezone(timedelta(hours=-7), name="YST")


LAYER_COLORS = {
    "Projects_Linear": "#f59e0b",
    "Projects_Placer": "#ef4444",
    "Projects_Points": "#10b981",
    "Projects_Polygons": "#3b82f6",
    "Projects_Quartz": "#8b5cf6",
}

LABEL_FIELDS = (
    "Prj_Name",
    "PROPERTY_N",
    "ProjectID",
    "Prj_ID",
    "YESAB_PROJ",
    "Number",
)

PROJECT_NUMBER_FIELDS = (
    "projectNumber",
    "ProjectID",
    "Prj_ID",
    "YESAB_PROJ",
    "Number",
)


def round_coord(value: float) -> float:
    """Round projected coordinates to a compact precision for browser delivery."""
    return round(value, 1)


def clean_props(record: dict[str, str]) -> dict[str, str]:
    """Drop blank DBF fields and normalize whitespace in the remaining values."""
    cleaned: dict[str, str] = {}
    for key, value in record.items():
        text = value.strip()
        if not text:
            continue
        cleaned[key] = text
    return cleaned


def label_for(record: dict[str, str], fallback: str) -> str:
    """Choose a human-friendly feature label from the preferred attribute fields."""
    for field in LABEL_FIELDS:
        value = record.get(field, "").strip()
        if value:
            return value
    return fallback


def project_number_for(record: dict[str, str]) -> str:
    """Return the first available project-number style identifier from a feature record."""
    for field in PROJECT_NUMBER_FIELDS:
        value = record.get(field, "").strip()
        if value:
            return value
    return ""


def utc_now_iso() -> str:
    """Return the current UTC timestamp in ISO-8601 format."""
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def format_yukon_time(value: str) -> str:
    """Convert ISO-8601 or RFC-1123 timestamps to Yukon Standard Time."""
    if not value:
        return ""
    parsed: datetime | None = None
    try:
        parsed = datetime.strptime(value, "%a, %d %b %Y %H:%M:%S GMT").replace(
            tzinfo=UTC
        )
    except ValueError:
        pass
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(YST).strftime("%Y-%m-%d %H:%M YST")


def read_json_file(path: Path) -> dict[str, object]:
    """Return JSON content from ``path`` when present, otherwise an empty mapping."""
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_source_info() -> dict[str, object]:
    """Return source links and currency dates embedded into the built page."""
    zip_state = read_json_file(ZIP_STATE_FILE)
    api_state = read_json_file(API_STATE_FILE)
    merged = api_state.get("merged", {}) if isinstance(api_state, dict) else {}
    return {
        "pageBuiltAt": format_yukon_time(utc_now_iso()),
        "shapefile": {
            "label": "YESAB Project Map File",
            "pageUrl": PROJECT_MAP_PAGE_URL,
            "dataUrl": PROJECT_MAP_ARCHIVE_URL,
            "sourceDate": format_yukon_time(zip_state.get("last_modified", ""))
            if isinstance(zip_state, dict)
            else "",
            "contentLength": zip_state.get("content_length", "")
            if isinstance(zip_state, dict)
            else "",
        },
        "registry": {
            "label": "YESAB Online Registry",
            "pageUrl": REGISTRY_FRONT_URL,
            "apiUrl": REGISTRY_API_URL,
            "sourceDate": format_yukon_time(merged.get("generatedAt", ""))
            if isinstance(merged, dict)
            else "",
            "bucketCount": merged.get("bucketCount", 0)
            if isinstance(merged, dict)
            else 0,
            "projectCount": merged.get("projectCount", 0)
            if isinstance(merged, dict)
            else 0,
        },
    }


def load_api_projects() -> dict[str, dict[str, object]]:
    """Load merged YESAB API records keyed by project number, if available."""
    if not API_CACHE_FILE.exists():
        return {}
    payload = json.loads(API_CACHE_FILE.read_text(encoding="utf-8"))
    projects = payload.get("projects", [])
    lookup: dict[str, dict[str, object]] = {}
    for project in projects:
        project_number = str(project.get("projectNumber", "")).strip()
        if project_number:
            lookup[project_number] = project
    return lookup


def qa_project_summary(project: dict[str, object]) -> dict[str, object]:
    """Return a compact QA summary for one cached API project."""
    return {
        "projectNumber": project.get("projectNumber", ""),
        "projectId": project.get("projectId", ""),
        "title": project.get("title", ""),
        "projectTypeName": project.get("projectTypeName", ""),
        "proponentName": project.get("proponentName", ""),
        "stageName": project.get("stage", {}).get("name", ""),
        "districts": [
            item.get("name", "") for item in project.get("assessmentDistricts", [])
        ],
        "sectors": [item.get("name", "") for item in project.get("sectors", [])],
        "locationCount": len(project.get("locations", [])),
    }


def build_qa_html(qa_payload: dict[str, object], title: str) -> str:
    """Render a lightweight HTML QA report for API-to-map matching."""
    matched = qa_payload["matchedProjects"]
    unmatched = qa_payload["unmatchedApiProjects"]
    matched_rows = "".join(
        f"<tr><td>{item['projectNumber']}</td><td>{item['featureCount']}</td><td>{item['layerNames']}</td></tr>"
        for item in matched[:80]
    )
    unmatched_rows = "".join(
        f"<tr><td>{item['projectNumber']}</td><td>{item['stageName']}</td><td>{item['title']}</td><td>{', '.join(item['districts'])}</td></tr>"
        for item in unmatched[:160]
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{title}</title>
  <style>
    body {{
      margin: 0;
      font-family: Georgia, "Times New Roman", serif;
      color: #1f2937;
      background: linear-gradient(135deg, #efe8d8, #f7f4ec 45%, #ebe4d5);
    }}
    main {{
      width: min(1200px, calc(100vw - 32px));
      margin: 20px auto 40px;
      display: grid;
      gap: 18px;
    }}
    section {{
      background: rgba(255,252,246,0.92);
      border: 1px solid rgba(31,41,55,0.12);
      border-radius: 20px;
      padding: 20px 22px;
    }}
    h1, h2 {{ margin: 0 0 12px; }}
    .stats {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
    }}
    .stat {{
      border: 1px solid rgba(31,41,55,0.08);
      border-radius: 16px;
      background: rgba(255,255,255,0.6);
      padding: 12px 14px;
    }}
    .stat strong {{ display:block; font-size: 1.4rem; }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 0.95rem;
    }}
    th, td {{
      text-align: left;
      vertical-align: top;
      padding: 9px 10px;
      border-top: 1px solid rgba(31,41,55,0.1);
    }}
    th {{
      color: #6b7280;
      font-weight: 700;
      border-top: 0;
    }}
    code {{ background: rgba(17,24,39,0.06); padding: 0.1em 0.35em; border-radius: 6px; }}
    @media (max-width: 900px) {{
      .stats {{ grid-template-columns: 1fr 1fr; }}
    }}
  </style>
</head>
<body>
  <main>
    <section>
      <h1>{title}</h1>
      <p>QA summary for cached YESAB API matching against shapefile-derived geometries.</p>
      <div class="stats">
        <div class="stat"><strong>{qa_payload["summary"]["cachedApiProjectCount"]}</strong><span>cached API projects</span></div>
        <div class="stat"><strong>{qa_payload["summary"]["matchedApiProjectCount"]}</strong><span>API projects matched to geometry</span></div>
        <div class="stat"><strong>{qa_payload["summary"]["unmatchedApiProjectCount"]}</strong><span>cached API projects with no geometry match</span></div>
        <div class="stat"><strong>{qa_payload["summary"]["matchedFeatureCount"]}</strong><span>geometry features linked to cached API records</span></div>
      </div>
      <p>Coverage: <code>{qa_payload["summary"]["matchedApiProjectCount"]}/{qa_payload["summary"]["cachedApiProjectCount"]}</code> cached API projects matched.</p>
    </section>
    <section>
      <h2>Matched Projects</h2>
      <table>
        <thead><tr><th>Project Number</th><th>Feature Count</th><th>Layers</th></tr></thead>
        <tbody>{matched_rows}</tbody>
      </table>
    </section>
    <section>
      <h2>Unmatched Cached API Projects</h2>
      <table>
        <thead><tr><th>Project Number</th><th>Stage</th><th>Title</th><th>Districts</th></tr></thead>
        <tbody>{unmatched_rows}</tbody>
      </table>
    </section>
  </main>
</body>
</html>
"""


def read_dbf(data: bytes) -> list[dict[str, str]]:
    """Parse a DBF table into a list of string-valued records."""
    num_records = struct.unpack("<I", data[4:8])[0]
    header_len = struct.unpack("<H", data[8:10])[0]
    record_len = struct.unpack("<H", data[10:12])[0]
    fields: list[tuple[str, str, int, int]] = []
    offset = 32
    while data[offset] != 0x0D:
        name = data[offset : offset + 11].split(b"\x00", 1)[0].decode("ascii", "ignore")
        field_type = chr(data[offset + 11])
        length = data[offset + 16]
        decimals = data[offset + 17]
        fields.append((name, field_type, length, decimals))
        offset += 32

    records: list[dict[str, str]] = []
    pos = header_len
    for _ in range(num_records):
        record = data[pos : pos + record_len]
        pos += record_len
        if not record or record[0] == 0x2A:
            continue
        row: dict[str, str] = {}
        cursor = 1
        for name, _field_type, length, _decimals in fields:
            raw = record[cursor : cursor + length]
            cursor += length
            row[name] = raw.decode("latin1", "ignore").strip()
        records.append(row)
    return records


def read_shp(data: bytes) -> list[dict[str, object]]:
    """Parse point, polyline, and polygon shapefile geometries with bounding boxes."""
    features: list[dict[str, object]] = []
    pos = 100
    while pos + 8 <= len(data):
        _record_number = struct.unpack(">i", data[pos : pos + 4])[0]
        content_len = struct.unpack(">i", data[pos + 4 : pos + 8])[0] * 2
        rec = data[pos + 8 : pos + 8 + content_len]
        pos += 8 + content_len
        if len(rec) < 4:
            continue
        shape_type = struct.unpack("<i", rec[0:4])[0]
        if shape_type == 0:
            continue
        if shape_type == 1:
            x, y = struct.unpack("<2d", rec[4:20])
            geometry = {
                "type": "Point",
                "coordinates": [round_coord(x), round_coord(y)],
            }
            bbox = [round_coord(x), round_coord(y), round_coord(x), round_coord(y)]
            features.append({"geometry": geometry, "bbox": bbox})
            continue
        if shape_type not in (3, 5):
            raise ValueError(f"Unsupported shape type: {shape_type}")

        xmin, ymin, xmax, ymax, num_parts, num_points = struct.unpack(
            "<4d2i", rec[4:44]
        )
        parts_idx = list(struct.unpack(f"<{num_parts}i", rec[44 : 44 + 4 * num_parts]))
        points_raw = rec[44 + 4 * num_parts : 44 + 4 * num_parts + 16 * num_points]
        points = [
            [round_coord(x), round_coord(y)]
            for x, y in struct.iter_unpack("<2d", points_raw)
        ]
        parts: list[list[list[float]]] = []
        for i, start in enumerate(parts_idx):
            end = parts_idx[i + 1] if i + 1 < len(parts_idx) else len(points)
            part = points[start:end]
            if part:
                parts.append(part)
        geometry_type = "LineString" if shape_type == 3 else "Polygon"
        features.append(
            {
                "geometry": {"type": geometry_type, "coordinates": parts},
                "bbox": [
                    round_coord(xmin),
                    round_coord(ymin),
                    round_coord(xmax),
                    round_coord(ymax),
                ],
            }
        )
    return features


def load_layers() -> dict[str, object]:
    """Load every zipped shapefile layer under ``data/`` into a browser payload."""
    layers: list[dict[str, object]] = []
    archives: list[str] = []
    bounds: list[float] | None = None
    api_projects = load_api_projects()
    matched_project_numbers: set[str] = set()
    matched_project_features: dict[str, int] = {}
    matched_project_layers: dict[str, set[str]] = {}
    matched_feature_count = 0

    for zip_path in sorted(DATA_DIR.glob("*.zip")):
        archives.append(zip_path.name)
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            shp_names = sorted(name for name in names if name.lower().endswith(".shp"))
            for shp_name in shp_names:
                stem = shp_name[:-4]
                dbf_name = f"{stem}.dbf"
                if dbf_name not in names:
                    continue
                feature_geoms = read_shp(zf.read(shp_name))
                records = read_dbf(zf.read(dbf_name))
                merged = []
                for idx, geom in enumerate(feature_geoms):
                    props = clean_props(records[idx] if idx < len(records) else {})
                    bbox = geom["bbox"]
                    project_number = project_number_for(props)
                    api_project_number = (
                        project_number if project_number in api_projects else ""
                    )
                    if api_project_number:
                        matched_project_numbers.add(api_project_number)
                    merged.append(
                        {
                            "id": idx + 1,
                            "label": label_for(props, f"{Path(stem).name} #{idx + 1}"),
                            "bbox": bbox,
                            "properties": props,
                            "geometry": geom["geometry"],
                            "apiProjectNumber": api_project_number,
                        }
                    )
                    if bounds is None:
                        bounds = list(bbox)
                    else:
                        bounds[0] = min(bounds[0], bbox[0])
                        bounds[1] = min(bounds[1], bbox[1])
                        bounds[2] = max(bounds[2], bbox[2])
                        bounds[3] = max(bounds[3], bbox[3])

                layer_name = Path(stem).name
                geom_type = merged[0]["geometry"]["type"] if merged else "Unknown"
                for feature in merged:
                    api_project_number = feature["apiProjectNumber"]
                    if api_project_number:
                        matched_feature_count += 1
                        matched_project_features[api_project_number] = (
                            matched_project_features.get(api_project_number, 0) + 1
                        )
                        matched_project_layers.setdefault(
                            api_project_number, set()
                        ).add(layer_name)
                layers.append(
                    {
                        "name": layer_name,
                        "archive": zip_path.name,
                        "color": LAYER_COLORS.get(layer_name, "#64748b"),
                        "type": geom_type,
                        "count": len(merged),
                        "features": merged,
                    }
                )

    if bounds is None:
        bounds = [0.0, 0.0, 1.0, 1.0]

    unmatched_project_numbers = sorted(set(api_projects) - matched_project_numbers)
    qa_payload = {
        "summary": {
            "cachedApiProjectCount": len(api_projects),
            "matchedApiProjectCount": len(matched_project_numbers),
            "unmatchedApiProjectCount": len(unmatched_project_numbers),
            "matchedFeatureCount": matched_feature_count,
        },
        "matchedProjects": [
            {
                "projectNumber": project_number,
                "featureCount": matched_project_features.get(project_number, 0),
                "layerNames": ", ".join(
                    sorted(matched_project_layers.get(project_number, set()))
                ),
            }
            for project_number in sorted(matched_project_numbers)
        ],
        "unmatchedApiProjects": [
            qa_project_summary(api_projects[project_number])
            for project_number in unmatched_project_numbers
        ],
    }

    return {
        "archives": archives,
        "bounds": bounds,
        "layers": layers,
        "apiProjects": api_projects,
        "sourceInfo": load_source_info(),
        "apiSummary": {
            "available": bool(api_projects),
            "projectCount": len(api_projects),
            "matchedProjectCount": len(matched_project_numbers),
            "matchedFeatureCount": matched_feature_count,
        },
        "qa": qa_payload,
    }


def build_html(payload: dict[str, object]) -> str:
    """Render the self-contained HTML application around the parsed layer payload."""
    data_json = json.dumps(payload, separators=(",", ":"))
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>YESAB Static Map</title>
  <style>
    :root {{
      --bg: #f3efe6;
      --panel: rgba(255, 252, 246, 0.92);
      --ink: #1f2937;
      --muted: #6b7280;
      --line: rgba(31, 41, 55, 0.12);
      --accent: #111827;
    }}
    * {{ box-sizing: border-box; }}
    html, body {{ height: 100%; margin: 0; }}
    body {{
      font-family: Georgia, "Times New Roman", serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(255,255,255,0.8), transparent 40%),
        linear-gradient(135deg, #efe8d8, #f7f4ec 45%, #ebe4d5);
    }}
    .app {{
      display: grid;
      grid-template-columns: minmax(280px, 360px) 1fr;
      height: 100%;
    }}
    .sidebar {{
      overflow: auto;
      padding: 20px 18px 24px;
      background: var(--panel);
      backdrop-filter: blur(10px);
      border-right: 1px solid var(--line);
    }}
    h1 {{
      font-size: 1.35rem;
      line-height: 1.1;
      margin: 0 0 10px;
      letter-spacing: 0.02em;
    }}
    p {{
      margin: 0 0 12px;
      color: var(--muted);
      line-height: 1.4;
    }}
    .toolbar {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      margin: 16px 0 18px;
    }}
    .subtools {{
      margin: -6px 0 16px;
      font-size: 0.88rem;
    }}
    .subtools a {{
      color: var(--accent);
      text-decoration-thickness: 1px;
      text-underline-offset: 2px;
    }}
    button {{
      appearance: none;
      border: 1px solid rgba(17, 24, 39, 0.16);
      background: #fffdf8;
      color: var(--accent);
      border-radius: 999px;
      padding: 8px 12px;
      font: inherit;
      cursor: pointer;
    }}
    button:hover {{ background: #fff; }}
    .meta, .details {{
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 12px 14px;
      background: rgba(255,255,255,0.55);
    }}
    .meta {{ margin-bottom: 14px; }}
    .layers {{
      display: grid;
      gap: 10px;
      margin-bottom: 14px;
    }}
    .layer {{
      display: grid;
      grid-template-columns: auto 1fr auto;
      gap: 10px;
      align-items: center;
      padding: 10px 12px;
      border: 1px solid var(--line);
      border-radius: 14px;
      background: rgba(255,255,255,0.6);
    }}
    .swatch {{
      width: 12px;
      height: 12px;
      border-radius: 999px;
      box-shadow: 0 0 0 1px rgba(0,0,0,0.08) inset;
    }}
    .layer strong {{
      display: block;
      font-size: 0.95rem;
    }}
    .layer span {{
      display: block;
      color: var(--muted);
      font-size: 0.83rem;
    }}
    .map-wrap {{
      position: relative;
      min-width: 0;
      overflow: hidden;
    }}
    canvas {{
      display: block;
      width: 100%;
      height: 100%;
    }}
    .tooltip {{
      position: absolute;
      pointer-events: none;
      z-index: 2;
      max-width: min(320px, calc(100% - 16px));
      background: rgba(17, 24, 39, 0.9);
      color: white;
      padding: 8px 10px;
      border-radius: 10px;
      font-size: 0.88rem;
      line-height: 1.35;
      display: none;
      white-space: normal;
      overflow-wrap: anywhere;
      box-shadow: 0 10px 24px rgba(17, 24, 39, 0.22);
    }}
    .about-panel {{
      position: absolute;
      top: 18px;
      right: 18px;
      z-index: 3;
      width: min(420px, calc(100% - 36px));
      max-height: calc(100% - 36px);
      overflow: auto;
      padding: 16px 18px;
      border: 1px solid var(--line);
      border-radius: 14px;
      background: rgba(255, 252, 246, 0.97);
      box-shadow: 0 18px 48px rgba(17, 24, 39, 0.2);
      display: none;
    }}
    .about-panel.open {{
      display: block;
    }}
    .about-panel h2 {{
      margin: 0 0 10px;
      font-size: 1rem;
    }}
    .about-panel p {{
      margin: 0 0 10px;
      font-size: 0.9rem;
      line-height: 1.45;
    }}
    .about-panel .close {{
      float: right;
      margin: -2px 0 10px 10px;
    }}
    .about-panel dl {{
      margin-bottom: 12px;
    }}
    .details h2 {{
      font-size: 1rem;
      margin: 0 0 10px;
    }}
    dl {{
      display: grid;
      grid-template-columns: minmax(84px, 110px) 1fr;
      gap: 6px 10px;
      margin: 0;
      font-size: 0.88rem;
    }}
    dt {{
      color: var(--muted);
      overflow-wrap: anywhere;
    }}
    dd {{
      margin: 0;
      overflow-wrap: anywhere;
    }}
    .hint {{
      margin-top: 12px;
      font-size: 0.82rem;
    }}
    @media (max-width: 920px) {{
      .app {{ grid-template-columns: 1fr; grid-template-rows: auto 1fr; }}
      .sidebar {{ max-height: 45vh; border-right: 0; border-bottom: 1px solid var(--line); }}
    }}
  </style>
</head>
<body>
  <div class="app">
    <aside class="sidebar">
      <h1>YESAB Project Map</h1>
      <p>Static self-contained viewer generated from zipped shapefiles in <code>data/</code>. It renders Yukon Albers coordinates directly, without external libraries or basemap tiles.</p>
      <div class="meta" id="meta"></div>
      <div class="toolbar">
        <button id="fitBtn" type="button">Fit All</button>
        <button id="toggleBtn" type="button">Toggle All</button>
      </div>
      <div class="subtools">
        <a href="#" id="aboutLink">About this map</a>
      </div>
      <div class="layers" id="layers"></div>
      <div class="details" id="details">
        <h2>Selection</h2>
        <p>Click a feature to inspect its attributes.</p>
      </div>
      <p class="hint">Pan by dragging. Zoom with the mouse wheel or trackpad.</p>
    </aside>
    <main class="map-wrap">
      <canvas id="map"></canvas>
      <div class="tooltip" id="tooltip"></div>
      <section class="about-panel" id="aboutPanel" aria-hidden="true">
        <button class="close" id="aboutClose" type="button">Close</button>
        <div id="aboutContent"></div>
      </section>
    </main>
  </div>
  <script>
    const DATA = {data_json};
    const canvas = document.getElementById("map");
    const ctx = canvas.getContext("2d");
    const tooltip = document.getElementById("tooltip");
    const aboutLink = document.getElementById("aboutLink");
    const aboutPanel = document.getElementById("aboutPanel");
    const aboutClose = document.getElementById("aboutClose");
    const aboutContent = document.getElementById("aboutContent");
    const layersEl = document.getElementById("layers");
    const detailsEl = document.getElementById("details");
    const metaEl = document.getElementById("meta");
    const fitBtn = document.getElementById("fitBtn");
    const toggleBtn = document.getElementById("toggleBtn");

    const state = {{
      visible: new Set(DATA.layers.map(layer => layer.name)),
      selected: null,
      hovered: null,
      dragging: false,
      lastX: 0,
      lastY: 0,
      scale: 1,
      tx: 0,
      ty: 0
    }};

    function esc(value) {{
      return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;");
    }}

    function listText(items, mapper) {{
      if (!items || !items.length) return "";
      return items.map(mapper).filter(Boolean).join(", ");
    }}

    function apiUrl(api) {{
      const key = api?.projectNumber || api?.projectId || "";
      return key ? `https://yesabregistry.ca/api/integration/projects/${{encodeURIComponent(key)}}` : "";
    }}

    function registryPageUrl(api) {{
      const key = api?.projectId || "";
      return key ? `https://yesabregistry.ca/projects/${{encodeURIComponent(key)}}` : "";
    }}

    function renderApiDetails(api) {{
      if (!api) return "";
      const districts = listText(api.assessmentDistricts, item => item.name);
      const sectors = listText(api.sectors, item => item.name);
      const governments = listText(api.indigenousGovernments, item => item);
      const decisionBodies = listText(api.decisionBodies, item => item);
      const planning = listText(api.planningCommissions, item => item);
      const outcome = api.outcomes?.outcomeName || "";
      const decision = api.outcomes?.decisionName || "";
      const stage = api.stage?.name || "";
      const daysRemaining = api.stage?.daysRemaining;
      const stageLabel = stage
        ? `${{stage}}${{Number.isFinite(daysRemaining) ? ` (${{daysRemaining}} days remaining)` : ""}}`
        : "";
      const rows = [
        ["Project Number", api.projectNumber],
        ["Project ID", api.projectId],
        ["Type", api.projectTypeName],
        ["Proponent", api.proponentName],
        ["Stage", stageLabel],
        ["Outcome", outcome],
        ["Decision", decision],
        ["Districts", districts],
        ["Sectors", sectors],
        ["Governments", governments],
        ["Decision Bodies", decisionBodies],
        ["Planning", planning],
        ["Stage History", api.stageHistory?.length ? `${{api.stageHistory.length}} entries` : ""],
        ["Locations", api.locations?.length ? `${{api.locations.length}} point(s)` : ""]
      ]
        .filter(([, value]) => value)
        .map(([key, value]) => `<dt>${{esc(key)}}</dt><dd>${{esc(value)}}</dd>`)
        .join("");
      const summary = api.projectScope?.summary ? `<p>${{esc(api.projectScope.summary)}}</p>` : "";
      const apiLink = apiUrl(api);
      const pageLink = registryPageUrl(api);
      const links = [
        pageLink ? `<a href="${{pageLink}}" target="_blank" rel="noreferrer">Registry page</a>` : "",
        apiLink ? `<a href="${{apiLink}}" target="_blank" rel="noreferrer">API record</a>` : "",
      ].filter(Boolean).join(" | ");
      const linkHtml = links ? `<p>${{links}}</p>` : "";
      return `
        <h2>Registry</h2>
        ${{api.title ? `<p><strong>${{esc(api.title)}}</strong></p>` : ""}}
        <dl>${{rows}}</dl>
        ${{summary}}
        ${{linkHtml}}
      `;
    }}

    function renderAbout() {{
      const info = DATA.sourceInfo || {{}};
      const shapefile = info.shapefile || {{}};
      const registry = info.registry || {{}};
      const bucketLine = registry.bucketCount
        ? `<dt>API cache coverage</dt><dd>${{esc(String(registry.bucketCount))}} bucket(s), ${{esc(String(registry.projectCount || 0))}} projects</dd>`
        : "";
      aboutContent.innerHTML = `
        <h2>About This Map</h2>
        <p>This page combines YESAB project-map shapefile geometry with cached YESAB registry metadata when a project-number match is available.</p>
        <p>This is not a finished product - it is a toy proof-of-concept that happens to have a little usefulness.</p>
        <dl>
          <dt>Page build date</dt><dd>${{esc(info.pageBuiltAt || "")}}</dd>
          <dt>Map file date</dt><dd>${{esc(shapefile.sourceDate || "Unknown")}}</dd>
          <dt>Registry cache date</dt><dd>${{esc(registry.sourceDate || "Not loaded")}}</dd>
          ${{bucketLine}}
        </dl>
        <p><a href="${{esc(shapefile.pageUrl || "#")}}" target="_blank" rel="noreferrer">YESAB Project Map File page</a></p>
        <p><a href="${{esc(registry.pageUrl || "#")}}" target="_blank" rel="noreferrer">YESAB Online Registry</a></p>
      `;
    }}

    function setAboutOpen(open) {{
      aboutPanel.classList.toggle("open", open);
      aboutPanel.setAttribute("aria-hidden", open ? "false" : "true");
    }}

    function resize() {{
      const dpr = window.devicePixelRatio || 1;
      const rect = canvas.getBoundingClientRect();
      canvas.width = Math.max(1, Math.round(rect.width * dpr));
      canvas.height = Math.max(1, Math.round(rect.height * dpr));
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      render();
    }}

    function fitBounds(bounds) {{
      const rect = canvas.getBoundingClientRect();
      const pad = 28;
      const w = Math.max(1, rect.width - pad * 2);
      const h = Math.max(1, rect.height - pad * 2);
      const dx = Math.max(1, bounds[2] - bounds[0]);
      const dy = Math.max(1, bounds[3] - bounds[1]);
      state.scale = Math.min(w / dx, h / dy);
      state.tx = pad + (w - dx * state.scale) / 2 - bounds[0] * state.scale;
      state.ty = pad + (h - dy * state.scale) / 2 + bounds[3] * state.scale;
      render();
    }}

    function worldToScreen(pt) {{
      return [pt[0] * state.scale + state.tx, state.ty - pt[1] * state.scale];
    }}

    function screenToWorld(x, y) {{
      return [(x - state.tx) / state.scale, (state.ty - y) / state.scale];
    }}

    function drawFeature(feature, color, selected) {{
      const geom = feature.geometry;
      const isHover = state.hovered === feature;
      const hasApi = Boolean(feature.apiProjectNumber);
      const alpha = selected ? 0.85 : isHover ? 0.72 : 0.48;
      const stroke = selected ? 2.2 : isHover ? 1.7 : 1.1;
      ctx.beginPath();
      if (geom.type === "Point") {{
        const [sx, sy] = worldToScreen(geom.coordinates);
        if (hasApi) {{
          ctx.beginPath();
          ctx.arc(sx, sy, selected ? 8.2 : 6.4, 0, Math.PI * 2);
          ctx.fillStyle = "rgba(180, 134, 47, 0.32)";
          ctx.fill();
          ctx.lineWidth = selected ? 2.3 : 1.7;
          ctx.strokeStyle = "rgba(31, 41, 55, 0.78)";
          ctx.stroke();
          ctx.beginPath();
        }}
        ctx.arc(sx, sy, selected ? 5 : 3.2, 0, Math.PI * 2);
        ctx.fillStyle = color;
        ctx.globalAlpha = 0.95;
        ctx.fill();
        ctx.globalAlpha = 1;
        if (selected || isHover) {{
          ctx.lineWidth = 2;
          ctx.strokeStyle = "rgba(17,24,39,0.85)";
          ctx.stroke();
        }}
        return;
      }}
      for (const part of geom.coordinates) {{
        part.forEach((point, index) => {{
          const [sx, sy] = worldToScreen(point);
          if (index === 0) ctx.moveTo(sx, sy);
          else ctx.lineTo(sx, sy);
        }});
        if (geom.type === "Polygon" && part.length) {{
          const [sx, sy] = worldToScreen(part[0]);
          ctx.lineTo(sx, sy);
        }}
      }}
      if (geom.type === "Polygon") {{
        ctx.fillStyle = color;
        ctx.globalAlpha = alpha * 0.45;
        ctx.fill("evenodd");
        ctx.globalAlpha = 1;
      }}
      if (hasApi) {{
        ctx.save();
        ctx.lineWidth = stroke + 2.4;
        ctx.strokeStyle = "rgba(180, 134, 47, 0.7)";
        ctx.globalAlpha = selected ? 0.9 : 0.58;
        ctx.stroke();
        ctx.restore();
      }}
      ctx.lineWidth = stroke;
      ctx.strokeStyle = color;
      ctx.globalAlpha = alpha;
      ctx.stroke();
      ctx.globalAlpha = 1;
    }}

    function drawGrid() {{
      const rect = canvas.getBoundingClientRect();
      const [wx0, wy0] = screenToWorld(0, rect.height);
      const [wx1, wy1] = screenToWorld(rect.width, 0);
      const span = Math.max(wx1 - wx0, wy1 - wy0);
      const rawStep = span / 8;
      const mag = Math.pow(10, Math.floor(Math.log10(rawStep)));
      const norm = rawStep / mag;
      const step = (norm < 2 ? 1 : norm < 5 ? 2 : 5) * mag;
      const startX = Math.floor(wx0 / step) * step;
      const startY = Math.floor(wy0 / step) * step;

      ctx.save();
      ctx.strokeStyle = "rgba(31, 41, 55, 0.08)";
      ctx.lineWidth = 1;
      for (let x = startX; x <= wx1; x += step) {{
        const [sx0, sy0] = worldToScreen([x, wy0]);
        const [sx1, sy1] = worldToScreen([x, wy1]);
        ctx.beginPath();
        ctx.moveTo(sx0, sy0);
        ctx.lineTo(sx1, sy1);
        ctx.stroke();
      }}
      for (let y = startY; y <= wy1; y += step) {{
        const [sx0, sy0] = worldToScreen([wx0, y]);
        const [sx1, sy1] = worldToScreen([wx1, y]);
        ctx.beginPath();
        ctx.moveTo(sx0, sy0);
        ctx.lineTo(sx1, sy1);
        ctx.stroke();
      }}
      ctx.restore();
    }}

    function render() {{
      const rect = canvas.getBoundingClientRect();
      ctx.clearRect(0, 0, rect.width, rect.height);
      const bg = ctx.createLinearGradient(0, 0, rect.width, rect.height);
      bg.addColorStop(0, "#fbf9f3");
      bg.addColorStop(1, "#eee6d5");
      ctx.fillStyle = bg;
      ctx.fillRect(0, 0, rect.width, rect.height);
      drawGrid();
      for (const layer of DATA.layers) {{
        if (!state.visible.has(layer.name)) continue;
        for (const feature of layer.features) {{
          drawFeature(feature, layer.color, state.selected === feature);
        }}
      }}
    }}

    function pointInRing(point, ring) {{
      let inside = false;
      for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {{
        const xi = ring[i][0], yi = ring[i][1];
        const xj = ring[j][0], yj = ring[j][1];
        const intersect = ((yi > point[1]) !== (yj > point[1])) &&
          (point[0] < ((xj - xi) * (point[1] - yi)) / ((yj - yi) || 1e-9) + xi);
        if (intersect) inside = !inside;
      }}
      return inside;
    }}

    function distToSegmentSq(p, a, b) {{
      const dx = b[0] - a[0];
      const dy = b[1] - a[1];
      if (dx === 0 && dy === 0) {{
        const px = p[0] - a[0];
        const py = p[1] - a[1];
        return px * px + py * py;
      }}
      const t = Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy)));
      const qx = a[0] + t * dx;
      const qy = a[1] + t * dy;
      const px = p[0] - qx;
      const py = p[1] - qy;
      return px * px + py * py;
    }}

    function hitTestFeature(feature, world, tolWorld) {{
      const b = feature.bbox;
      if (world[0] < b[0] - tolWorld || world[0] > b[2] + tolWorld || world[1] < b[1] - tolWorld || world[1] > b[3] + tolWorld) {{
        return false;
      }}
      const geom = feature.geometry;
      if (geom.type === "Point") {{
        const dx = world[0] - geom.coordinates[0];
        const dy = world[1] - geom.coordinates[1];
        return dx * dx + dy * dy <= tolWorld * tolWorld;
      }}
      if (geom.type === "Polygon") {{
        for (const ring of geom.coordinates) {{
          if (ring.length > 2 && pointInRing(world, ring)) return true;
        }}
      }}
      const tolSq = tolWorld * tolWorld;
      for (const part of geom.coordinates) {{
        for (let i = 1; i < part.length; i++) {{
          if (distToSegmentSq(world, part[i - 1], part[i]) <= tolSq) return true;
        }}
      }}
      return false;
    }}

    function pickFeature(clientX, clientY) {{
      const rect = canvas.getBoundingClientRect();
      const x = clientX - rect.left;
      const y = clientY - rect.top;
      const world = screenToWorld(x, y);
      const tolWorld = 8 / state.scale;
      const visibleLayers = DATA.layers.filter(layer => state.visible.has(layer.name));
      for (let li = visibleLayers.length - 1; li >= 0; li--) {{
        const layer = visibleLayers[li];
        for (let i = layer.features.length - 1; i >= 0; i--) {{
          const feature = layer.features[i];
          if (hitTestFeature(feature, world, tolWorld)) {{
            return {{ layer, feature, x, y }};
          }}
        }}
      }}
      return null;
    }}

    function updateDetails(selection) {{
      if (!selection) {{
        detailsEl.innerHTML = '<h2>Selection</h2><p>Click a feature to inspect its attributes.</p>';
        return;
      }}
      const rows = Object.entries(selection.feature.properties)
        .map(([key, value]) => `<dt>${{esc(key)}}</dt><dd>${{esc(value)}}</dd>`)
        .join("");
      const api = selection.feature.apiProjectNumber ? DATA.apiProjects[selection.feature.apiProjectNumber] : null;
      detailsEl.innerHTML = `
        <h2>${{esc(selection.feature.label)}}</h2>
        <p>${{esc(selection.layer.name)}} from ${{esc(selection.layer.archive)}}</p>
        <dl>${{rows}}</dl>
        ${{renderApiDetails(api)}}
      `;
    }}

    function updateTooltip(pick, clientX, clientY) {{
      if (!pick) {{
        tooltip.style.display = "none";
        return;
      }}
      tooltip.innerHTML = `<strong>${{esc(pick.feature.label)}}</strong><br>${{esc(pick.layer.name)}}${{pick.feature.apiProjectNumber ? "<br>Registry-linked" : ""}}`;
      tooltip.style.display = "block";
      const mapRect = canvas.parentElement.getBoundingClientRect();
      const margin = 8;
      const offset = 14;
      const localX = clientX - mapRect.left;
      const localY = clientY - mapRect.top;
      const width = tooltip.offsetWidth;
      const height = tooltip.offsetHeight;
      let left = localX + offset;
      let top = localY + offset;
      if (left + width > mapRect.width - margin) left = localX - width - offset;
      if (top + height > mapRect.height - margin) top = localY - height - offset;
      left = Math.max(margin, Math.min(left, mapRect.width - width - margin));
      top = Math.max(margin, Math.min(top, mapRect.height - height - margin));
      tooltip.style.left = `${{left}}px`;
      tooltip.style.top = `${{top}}px`;
    }}

    function renderLayerList() {{
      layersEl.innerHTML = "";
      for (const layer of DATA.layers) {{
        const row = document.createElement("label");
        row.className = "layer";
        row.innerHTML = `
          <input type="checkbox" ${{state.visible.has(layer.name) ? "checked" : ""}}>
          <div>
            <strong>${{esc(layer.name)}}</strong>
            <span>${{layer.count.toLocaleString()}} ${{esc(layer.type)}} features</span>
          </div>
          <div class="swatch" style="background:${{layer.color}}"></div>
        `;
        row.querySelector("input").addEventListener("change", (event) => {{
          if (event.target.checked) state.visible.add(layer.name);
          else state.visible.delete(layer.name);
          render();
        }});
        layersEl.appendChild(row);
      }}
    }}

    function renderMeta() {{
      const total = DATA.layers.reduce((sum, layer) => sum + layer.count, 0);
      const apiLine = DATA.apiSummary?.available
        ? `<span>${{DATA.apiSummary.matchedProjectCount.toLocaleString()}} map projects matched to ${{DATA.apiSummary.projectCount.toLocaleString()}} cached API projects</span><br><span>${{DATA.apiSummary.matchedFeatureCount.toLocaleString()}} geometry features are registry-linked and highlighted in gold</span><br>`
        : '<span>No cached API data loaded</span><br>';
      metaEl.innerHTML = `
        <strong>${{total.toLocaleString()}} features</strong><br>
        <span>${{DATA.layers.length}} layers across ${{DATA.archives.length}} archive(s)</span><br>
        ${{apiLine}}
        <span>${{DATA.archives.map(esc).join(", ")}}</span>
      `;
    }}

    canvas.addEventListener("mousedown", (event) => {{
      state.dragging = true;
      state.lastX = event.clientX;
      state.lastY = event.clientY;
      updateTooltip(null, 0, 0);
    }});

    window.addEventListener("mouseup", () => {{
      state.dragging = false;
    }});

    window.addEventListener("mousemove", (event) => {{
      if (state.dragging) {{
        state.tx += event.clientX - state.lastX;
        state.ty += event.clientY - state.lastY;
        state.lastX = event.clientX;
        state.lastY = event.clientY;
        state.hovered = null;
        updateTooltip(null, 0, 0);
        render();
      }}
    }});

    canvas.addEventListener("mousemove", (event) => {{
      if (state.dragging) return;
      const pick = pickFeature(event.clientX, event.clientY);
      state.hovered = pick ? pick.feature : null;
      updateTooltip(pick, event.clientX, event.clientY);
      render();
    }});

    canvas.addEventListener("mouseleave", () => {{
      state.hovered = null;
      updateTooltip(null, 0, 0);
      render();
    }});

    aboutLink.addEventListener("click", (event) => {{
      event.preventDefault();
      setAboutOpen(!aboutPanel.classList.contains("open"));
    }});

    aboutClose.addEventListener("click", () => {{
      setAboutOpen(false);
    }});

    canvas.addEventListener("click", (event) => {{
      const pick = pickFeature(event.clientX, event.clientY);
      state.selected = pick ? pick.feature : null;
      updateDetails(pick);
      render();
    }});

    canvas.addEventListener("wheel", (event) => {{
      event.preventDefault();
      const rect = canvas.getBoundingClientRect();
      const x = event.clientX - rect.left;
      const y = event.clientY - rect.top;
      const wx = (x - state.tx) / state.scale;
      const wy = (state.ty - y) / state.scale;
      const factor = Math.exp(-event.deltaY * 0.001);
      const nextScale = Math.max(0.00002, Math.min(30, state.scale * factor));
      state.scale = nextScale;
      state.tx = x - wx * state.scale;
      state.ty = y + wy * state.scale;
      render();
    }}, {{ passive: false }});

    fitBtn.addEventListener("click", () => fitBounds(DATA.bounds));

    toggleBtn.addEventListener("click", () => {{
      if (state.visible.size === DATA.layers.length) state.visible.clear();
      else DATA.layers.forEach(layer => state.visible.add(layer.name));
      renderLayerList();
      render();
    }});

    renderLayerList();
    renderMeta();
    renderAbout();
    resize();
    fitBounds(DATA.bounds);
    window.addEventListener("resize", resize);
  </script>
</body>
</html>
"""


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for the output directory."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "output_path",
        nargs="?",
        default=DEFAULT_OUTPUT_PATH,
        type=Path,
        help="HTML file path or output directory (default: ./out/yesab-map-in-one.html).",
    )
    return parser.parse_args()


def main() -> None:
    """Generate the single-file HTML map artifact."""
    args = parse_args()
    raw_output_path = args.output_path
    if raw_output_path.suffix.lower() == ".html":
        output_path = raw_output_path
    else:
        output_path = raw_output_path / "yesab-map-in-one.html"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = load_layers()
    output_path.write_text(build_html(payload), encoding="utf-8")
    qa_json_path = output_path.with_suffix(".qa.json")
    qa_html_path = output_path.with_suffix(".qa.html")
    qa_json_path.write_text(
        json.dumps(payload["qa"], indent=2, sort_keys=True), encoding="utf-8"
    )
    qa_html_path.write_text(
        build_qa_html(payload["qa"], "YESAB API Match QA"), encoding="utf-8"
    )
    total = sum(layer["count"] for layer in payload["layers"])
    print(
        f"Wrote {output_path} with {len(payload['layers'])} layers and {total} features."
    )
    print(f"Wrote QA artifacts: {qa_html_path.name}, {qa_json_path.name}")


if __name__ == "__main__":
    main()
