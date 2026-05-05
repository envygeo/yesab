"""Build a USB-safe multi-file static map from zipped shapefiles in ``data/``.

This variant separates HTML, CSS, application logic, and layer data into
multiple files while still avoiding ``fetch()`` so the site works over
``file://`` as well as from a static web server.

(c)2026 Matt Wilkie, Yukon Government. MIT License.
"""

# /// script
# requires-python = ">=3.14"
# ///
from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import struct
import zipfile
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import compression.zstd as zstd

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
API_CACHE_FILE = DATA_DIR / "api" / "projects_merged.json.zst"
API_STATE_FILE = DATA_DIR / "api" / "state.json"
API_LOCATION_OVERRIDES_FILE = DATA_DIR / "api" / "location_overrides.csv"
ZIP_STATE_FILE = DATA_DIR / "yesab_all_zip.state.json"
DEFAULT_OUTPUT_DIR = Path("./out/yesab-map")
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
API_FALLBACK_LAYER_NAME = "API_Approximate_Points"
API_FALLBACK_LAYER_COLOR = "#0f766e"
BAD_COORDINATE_DISPLAY_LATITUDE = 65.0
BAD_COORDINATE_DISPLAY_LONGITUDE = -127.0
YUKON_LATITUDE_RANGE = (59.0, 70.5)
YUKON_LONGITUDE_RANGE = (-142.5, -123.0)
GENERIC_LONGITUDES = (-141.00001, -140.00001, -124.00001)
GRS80_A = 6378137.0
GRS80_INV_F = 298.257222101
YUKON_ALBERS_FALSE_EASTING = 500000.0
YUKON_ALBERS_FALSE_NORTHING = 500000.0
YUKON_ALBERS_CENTRAL_MERIDIAN = math.radians(-132.5)
YUKON_ALBERS_STANDARD_PARALLEL_1 = math.radians(61.66666666666666)
YUKON_ALBERS_STANDARD_PARALLEL_2 = math.radians(68.0)
YUKON_ALBERS_LATITUDE_OF_ORIGIN = math.radians(59.0)

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


def albers_q(phi: float, eccentricity: float) -> float:
    """Return the ellipsoidal q term used by Albers equal-area projection."""
    sin_phi = math.sin(phi)
    e_sin_phi = eccentricity * sin_phi
    return (1 - eccentricity**2) * (
        sin_phi / (1 - e_sin_phi * e_sin_phi)
        - (1 / (2 * eccentricity)) * math.log((1 - e_sin_phi) / (1 + e_sin_phi))
    )


def albers_m(phi: float, eccentricity: float) -> float:
    """Return the ellipsoidal m term used by Albers equal-area projection."""
    sin_phi = math.sin(phi)
    return math.cos(phi) / math.sqrt(1 - eccentricity**2 * sin_phi * sin_phi)


def project_lonlat_to_yukon_albers(longitude: float, latitude: float) -> list[float]:
    """Project WGS84/NAD83-style lon/lat to the Yukon Albers map coordinates."""
    flattening = 1 / GRS80_INV_F
    eccentricity = math.sqrt(2 * flattening - flattening * flattening)
    m1 = albers_m(YUKON_ALBERS_STANDARD_PARALLEL_1, eccentricity)
    m2 = albers_m(YUKON_ALBERS_STANDARD_PARALLEL_2, eccentricity)
    q0 = albers_q(YUKON_ALBERS_LATITUDE_OF_ORIGIN, eccentricity)
    q1 = albers_q(YUKON_ALBERS_STANDARD_PARALLEL_1, eccentricity)
    q2 = albers_q(YUKON_ALBERS_STANDARD_PARALLEL_2, eccentricity)
    q = albers_q(math.radians(latitude), eccentricity)
    n = (m1 * m1 - m2 * m2) / (q2 - q1)
    c = m1 * m1 + n * q1
    rho0 = GRS80_A * math.sqrt(c - n * q0) / n
    rho = GRS80_A * math.sqrt(max(0.0, c - n * q)) / n
    theta = n * (math.radians(longitude) - YUKON_ALBERS_CENTRAL_MERIDIAN)
    x = YUKON_ALBERS_FALSE_EASTING + rho * math.sin(theta)
    y = YUKON_ALBERS_FALSE_NORTHING + rho0 - rho * math.cos(theta)
    return [round_coord(x), round_coord(y)]


def decimal_places(value: float) -> int:
    """Return the number of decimal places needed to represent a coordinate."""
    text = f"{value:.10f}".rstrip("0").rstrip(".")
    if "." not in text:
        return 0
    return len(text.split(".", maxsplit=1)[1])


def is_world_coordinate(latitude: float, longitude: float) -> bool:
    """Return true when the coordinate is valid lon/lat anywhere on earth."""
    return -90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0


def is_yukon_coordinate(latitude: float, longitude: float) -> bool:
    """Return true when the coordinate is inside a broad Yukon map range."""
    return (
        YUKON_LATITUDE_RANGE[0] <= latitude <= YUKON_LATITUDE_RANGE[1]
        and YUKON_LONGITUDE_RANGE[0] <= longitude <= YUKON_LONGITUDE_RANGE[1]
    )


def classify_api_coordinate(
    latitude: float, longitude: float, coordinate_count: int
) -> tuple[str, list[str]]:
    """Classify an API fallback coordinate for QA and post-processing."""
    if not is_world_coordinate(latitude, longitude):
        return "bad_coordinates", ["outside_world_range"]
    if not is_yukon_coordinate(latitude, longitude):
        return "bad_coordinates", ["outside_yukon_range"]

    flags: list[str] = []
    if coordinate_count >= 5:
        flags.append("repeated_coordinate_5plus")
    if any(abs(longitude - item) < 0.000001 for item in GENERIC_LONGITUDES):
        flags.append("sentinel_like_longitude")
    if (
        abs(latitude - round(latitude)) < 0.00011
        and abs(longitude - round(longitude)) < 0.00011
    ):
        flags.append("near_integer_coordinate")
    if flags:
        return "generic_coordinates", flags

    if decimal_places(latitude) <= 2 or decimal_places(longitude) <= 2:
        return "low_precision_coordinates", ["low_precision_2dp"]
    return "plausible_api_coordinates", []


def load_api_location_overrides() -> dict[tuple[str, str], tuple[float, float]]:
    """Load API coordinate overrides keyed by project number and project ID."""
    if not API_LOCATION_OVERRIDES_FILE.exists():
        return {}
    overrides: dict[tuple[str, str], tuple[float, float]] = {}
    with API_LOCATION_OVERRIDES_FILE.open(newline="", encoding="utf-8-sig") as handle:
        rows = (
            line
            for line in handle
            if line.strip() and not line.lstrip().startswith(("#", ";"))
        )
        for row in csv.DictReader(rows):
            project_number = (row.get("ProjectNumber") or "").strip()
            project_id = (row.get("ProjectID") or "").strip()
            if not project_number or not project_id:
                continue
            try:
                latitude = float((row.get("Replace_Lat") or "").strip())
                longitude = float((row.get("Replace_Long") or "").strip())
            except ValueError:
                continue
            overrides[(project_number, project_id)] = (latitude, longitude)
    return overrides


def clean_props(record: dict[str, str]) -> dict[str, str]:
    """Drop blank DBF fields and normalize whitespace in the remaining values."""
    return {key: value.strip() for key, value in record.items() if value.strip()}


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


def read_zstd_json_file(path: Path) -> dict[str, object]:
    """Return compressed JSON content from ``path`` when present, otherwise an empty mapping."""
    if not path.exists():
        return {}
    with zstd.open(path, "rt", encoding="utf-8") as handle:
        loaded = json.load(handle)
    return loaded if isinstance(loaded, dict) else {}


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
    payload = read_zstd_json_file(API_CACHE_FILE)
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


def api_fallback_feature(
    project: dict[str, object],
    feature_id: int,
    coordinate_counts: dict[tuple[float, float], int],
    location_overrides: dict[tuple[str, str], tuple[float, float]],
) -> dict[str, object] | None:
    """Build one approximate map point from an API project location."""
    project_number = str(project.get("projectNumber", "")).strip()
    project_id = str(project.get("projectId", "")).strip()
    if not project_number:
        return None
    for location in project.get("locations", []):
        if not isinstance(location, dict):
            continue
        latitude = location.get("latitude")
        longitude = location.get("longitude")
        if latitude is None or longitude is None:
            continue
        try:
            source_latitude = float(latitude)
            source_longitude = float(longitude)
        except (TypeError, ValueError):
            continue
        coordinate_key = (round(source_latitude, 5), round(source_longitude, 5))
        coordinate_class, coordinate_flags = classify_api_coordinate(
            source_latitude,
            source_longitude,
            coordinate_counts.get(coordinate_key, 1),
        )
        map_latitude = source_latitude
        map_longitude = source_longitude
        override = location_overrides.get((project_number, project_id))
        coordinate_override = ""
        if override is not None:
            map_latitude, map_longitude = override
            coordinate_override = "location_overrides.csv"
        elif coordinate_class == "bad_coordinates":
            map_latitude = BAD_COORDINATE_DISPLAY_LATITUDE
            map_longitude = BAD_COORDINATE_DISPLAY_LONGITUDE
            coordinate_override = "bad_coordinate_display_fallback"
        try:
            point = project_lonlat_to_yukon_albers(map_longitude, map_latitude)
        except (TypeError, ValueError):
            continue
        properties = {
            "projectNumber": project_number,
            "projectId": project_id,
            "title": str(project.get("title", "")).strip(),
            "projectTypeName": str(project.get("projectTypeName", "")).strip(),
            "proponentName": str(project.get("proponentName", "")).strip(),
            "stage": str(project.get("stage", {}).get("name", "")).strip(),
            "locationSource": "YESAB API location",
            "locationApproximate": "Yes",
            "locationCoordinateClass": coordinate_class,
            "locationCoordinateFlags": ", ".join(coordinate_flags),
            "locationCoordinateOverride": coordinate_override,
            "latitude": str(map_latitude),
            "longitude": str(map_longitude),
            "sourceLatitude": str(source_latitude),
            "sourceLongitude": str(source_longitude),
        }
        return {
            "id": feature_id,
            "label": str(project.get("title", "")).strip() or project_number,
            "bbox": [point[0], point[1], point[0], point[1]],
            "properties": {key: value for key, value in properties.items() if value},
            "geometry": {"type": "Point", "coordinates": point},
            "apiProjectNumber": project_number,
            "isApiFallback": True,
        }
    return None


def build_qa_html(qa_payload: dict[str, object], title: str) -> str:
    """Render a lightweight HTML QA report for API-to-map matching."""
    matched = qa_payload["matchedProjects"]
    fallback = qa_payload["fallbackApiProjects"]
    unmapped = qa_payload["unmappedApiProjects"]
    matched_rows = "".join(
        f"<tr><td>{item['projectNumber']}</td><td>{item['featureCount']}</td><td>{item['layerNames']}</td></tr>"
        for item in matched[:80]
    )
    fallback_rows = "".join(
        f"<tr><td>{item['projectNumber']}</td><td>{item.get('locationCoordinateClass', '')}</td><td>{item['stageName']}</td><td>{item['title']}</td><td>{', '.join(item['districts'])}</td></tr>"
        for item in fallback[:160]
    )
    unmapped_rows = "".join(
        f"<tr><td>{item['projectNumber']}</td><td>{item['stageName']}</td><td>{item['title']}</td><td>{item['locationCount']}</td></tr>"
        for item in unmapped[:160]
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
      <p>QA summary for cached YESAB API matching against shapefile-derived geometries, with approximate API-only fallback points where available.</p>
      <div class="stats">
        <div class="stat"><strong>{qa_payload["summary"]["cachedApiProjectCount"]}</strong><span>cached API projects</span></div>
        <div class="stat"><strong>{qa_payload["summary"]["matchedApiProjectCount"]}</strong><span>API projects with shapefile geometry</span></div>
        <div class="stat"><strong>{qa_payload["summary"]["fallbackApiProjectCount"]}</strong><span>API-only fallback point projects</span></div>
        <div class="stat"><strong>{qa_payload["summary"]["unmappedApiProjectCount"]}</strong><span>API projects still unmapped</span></div>
        <div class="stat"><strong>{qa_payload["summary"]["matchedFeatureCount"]}</strong><span>geometry features linked to cached API records</span></div>
      </div>
      <p>Coverage: <code>{qa_payload["summary"]["mappedApiProjectCount"]}/{qa_payload["summary"]["cachedApiProjectCount"]}</code> cached API projects shown on the map.</p>
    </section>
    <section>
      <h2>Matched Projects</h2>
      <table>
        <thead><tr><th>Project Number</th><th>Feature Count</th><th>Layers</th></tr></thead>
        <tbody>{matched_rows}</tbody>
      </table>
    </section>
    <section>
      <h2>API Fallback Points</h2>
      <table>
        <thead><tr><th>Project Number</th><th>Coordinate Class</th><th>Stage</th><th>Title</th><th>Districts</th></tr></thead>
        <tbody>{fallback_rows}</tbody>
      </table>
    </section>
    <section>
      <h2>Still Unmapped Cached API Projects</h2>
      <table>
        <thead><tr><th>Project Number</th><th>Stage</th><th>Title</th><th>API Locations</th></tr></thead>
        <tbody>{unmapped_rows}</tbody>
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
    fields: list[tuple[str, int]] = []
    offset = 32
    while data[offset] != 0x0D:
        name = data[offset : offset + 11].split(b"\x00", 1)[0].decode("ascii", "ignore")
        length = data[offset + 16]
        fields.append((name, length))
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
        for name, length in fields:
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
            features.append(
                {
                    "geometry": {
                        "type": "Point",
                        "coordinates": [round_coord(x), round_coord(y)],
                    },
                    "bbox": [
                        round_coord(x),
                        round_coord(y),
                        round_coord(x),
                        round_coord(y),
                    ],
                }
            )
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
        features.append(
            {
                "geometry": {
                    "type": "LineString" if shape_type == 3 else "Polygon",
                    "coordinates": parts,
                },
                "bbox": [
                    round_coord(xmin),
                    round_coord(ymin),
                    round_coord(xmax),
                    round_coord(ymax),
                ],
            }
        )
    return features


def load_payload() -> dict[str, object]:
    """Load every zipped shapefile layer under ``data/`` into a split-site payload."""
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
                features = []
                for idx, geom in enumerate(feature_geoms):
                    props = clean_props(records[idx] if idx < len(records) else {})
                    bbox = geom["bbox"]
                    project_number = project_number_for(props)
                    api_project_number = (
                        project_number if project_number in api_projects else ""
                    )
                    if api_project_number:
                        matched_project_numbers.add(api_project_number)
                    features.append(
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
                geom_type = features[0]["geometry"]["type"] if features else "Unknown"
                for feature in features:
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
                        "count": len(features),
                        "features": features,
                    }
                )

    unmatched_project_numbers = sorted(set(api_projects) - matched_project_numbers)
    location_overrides = load_api_location_overrides()
    coordinate_counts: dict[tuple[float, float], int] = {}
    for project_number in unmatched_project_numbers:
        for location in api_projects[project_number].get("locations", []):
            if not isinstance(location, dict):
                continue
            latitude = location.get("latitude")
            longitude = location.get("longitude")
            if latitude is None or longitude is None:
                continue
            try:
                coordinate_key = (round(float(latitude), 5), round(float(longitude), 5))
            except (TypeError, ValueError):
                continue
            coordinate_counts[coordinate_key] = (
                coordinate_counts.get(coordinate_key, 0) + 1
            )
            break
    fallback_features: list[dict[str, object]] = []
    fallback_project_numbers: list[str] = []
    fallback_project_summaries: list[dict[str, object]] = []
    unmapped_project_numbers: list[str] = []
    for project_number in unmatched_project_numbers:
        feature = api_fallback_feature(
            api_projects[project_number],
            len(fallback_features) + 1,
            coordinate_counts,
            location_overrides,
        )
        if feature is None:
            unmapped_project_numbers.append(project_number)
            continue
        fallback_features.append(feature)
        fallback_project_numbers.append(project_number)
        summary = qa_project_summary(api_projects[project_number])
        properties = feature["properties"]
        summary.update(
            {
                "locationCoordinateClass": properties.get(
                    "locationCoordinateClass", ""
                ),
                "locationCoordinateFlags": properties.get(
                    "locationCoordinateFlags", ""
                ),
                "locationCoordinateOverride": properties.get(
                    "locationCoordinateOverride", ""
                ),
                "latitude": properties.get("latitude", ""),
                "longitude": properties.get("longitude", ""),
                "sourceLatitude": properties.get("sourceLatitude", ""),
                "sourceLongitude": properties.get("sourceLongitude", ""),
            }
        )
        fallback_project_summaries.append(summary)
        bbox = feature["bbox"]
        if bounds is None:
            bounds = list(bbox)
        else:
            bounds[0] = min(bounds[0], bbox[0])
            bounds[1] = min(bounds[1], bbox[1])
            bounds[2] = max(bounds[2], bbox[2])
            bounds[3] = max(bounds[3], bbox[3])
    if fallback_features:
        layers.append(
            {
                "name": API_FALLBACK_LAYER_NAME,
                "archive": "YESAB API cache",
                "color": API_FALLBACK_LAYER_COLOR,
                "type": "Point",
                "count": len(fallback_features),
                "features": fallback_features,
            }
        )
    qa_payload = {
        "summary": {
            "cachedApiProjectCount": len(api_projects),
            "matchedApiProjectCount": len(matched_project_numbers),
            "fallbackApiProjectCount": len(fallback_project_numbers),
            "mappedApiProjectCount": len(matched_project_numbers)
            + len(fallback_project_numbers),
            "unmappedApiProjectCount": len(unmapped_project_numbers),
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
        "fallbackApiProjects": fallback_project_summaries,
        "unmappedApiProjects": [
            qa_project_summary(api_projects[project_number])
            for project_number in unmapped_project_numbers
        ],
    }
    return {
        "archives": archives,
        "bounds": bounds or [0.0, 0.0, 1.0, 1.0],
        "layers": layers,
        "apiProjects": api_projects,
        "sourceInfo": load_source_info(),
        "apiSummary": {
            "available": bool(api_projects),
            "projectCount": len(api_projects),
            "matchedProjectCount": len(matched_project_numbers),
            "fallbackProjectCount": len(fallback_project_numbers),
            "mappedProjectCount": len(matched_project_numbers)
            + len(fallback_project_numbers),
            "unmappedProjectCount": len(unmapped_project_numbers),
            "matchedFeatureCount": matched_feature_count,
        },
        "qa": qa_payload,
    }


def site_css() -> str:
    """Return the stylesheet for the split static map site."""
    return """\
:root {
  --bg: #f3efe6;
  --panel: rgba(255, 252, 246, 0.92);
  --ink: #1f2937;
  --muted: #6b7280;
  --line: rgba(31, 41, 55, 0.12);
  --accent: #111827;
}
* { box-sizing: border-box; }
html, body { height: 100%; margin: 0; }
body {
  font-family: Georgia, "Times New Roman", serif;
  color: var(--ink);
  background:
    radial-gradient(circle at top left, rgba(255,255,255,0.8), transparent 40%),
    linear-gradient(135deg, #efe8d8, #f7f4ec 45%, #ebe4d5);
}
.app {
  display: grid;
  grid-template-columns: minmax(280px, 360px) 1fr;
  height: 100%;
}
.sidebar {
  overflow: auto;
  padding: 20px 18px 24px;
  background: var(--panel);
  backdrop-filter: blur(10px);
  border-right: 1px solid var(--line);
}
h1 {
  font-size: 1.35rem;
  line-height: 1.1;
  margin: 0 0 10px;
  letter-spacing: 0.02em;
}
p {
  margin: 0 0 12px;
  color: var(--muted);
  line-height: 1.4;
}
.toolbar {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin: 16px 0 18px;
}
.subtools {
  margin: -6px 0 16px;
  font-size: 0.88rem;
}
.subtools a {
  color: var(--accent);
  text-decoration-thickness: 1px;
  text-underline-offset: 2px;
}
button {
  appearance: none;
  border: 1px solid rgba(17, 24, 39, 0.16);
  background: #fffdf8;
  color: var(--accent);
  border-radius: 999px;
  padding: 8px 12px;
  font: inherit;
  cursor: pointer;
}
button:hover { background: #fff; }
.meta, .details {
  border: 1px solid var(--line);
  border-radius: 16px;
  padding: 12px 14px;
  background: rgba(255,255,255,0.55);
}
.meta { margin-bottom: 14px; }
.layers {
  display: grid;
  gap: 10px;
  margin-bottom: 14px;
}
.layer {
  display: grid;
  grid-template-columns: auto 1fr auto;
  gap: 10px;
  align-items: center;
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-radius: 14px;
  background: rgba(255,255,255,0.6);
}
.swatch {
  width: 12px;
  height: 12px;
  border-radius: 999px;
  box-shadow: 0 0 0 1px rgba(0,0,0,0.08) inset;
}
.layer strong {
  display: block;
  font-size: 0.95rem;
}
.layer span {
  display: block;
  color: var(--muted);
  font-size: 0.83rem;
}
.map-wrap {
  position: relative;
  min-width: 0;
  overflow: hidden;
}
canvas {
  display: block;
  width: 100%;
  height: 100%;
}
.tooltip {
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
}
.about-panel {
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
}
.about-panel.open {
  display: block;
}
.about-panel h2 {
  margin: 0 0 10px;
  font-size: 1rem;
}
.about-panel p {
  margin: 0 0 10px;
  font-size: 0.9rem;
  line-height: 1.45;
}
.about-panel .close {
  float: right;
  margin: -2px 0 10px 10px;
}
.about-panel dl {
  margin-bottom: 12px;
}
.details h2 {
  font-size: 1rem;
  margin: 0 0 10px;
}
dl {
  display: grid;
  grid-template-columns: minmax(84px, 110px) 1fr;
  gap: 6px 10px;
  margin: 0;
  font-size: 0.88rem;
}
dt {
  color: var(--muted);
  overflow-wrap: anywhere;
}
dd {
  margin: 0;
  overflow-wrap: anywhere;
}
.hint {
  margin-top: 12px;
  font-size: 0.82rem;
}
@media (max-width: 920px) {
  .app { grid-template-columns: 1fr; grid-template-rows: auto 1fr; }
  .sidebar { max-height: 45vh; border-right: 0; border-bottom: 1px solid var(--line); }
}
"""


def site_html(layer_scripts: list[str], include_api_projects: bool) -> str:
    """Return the split-site HTML shell and script tags for each layer file."""
    scripts = "\n".join(
        f'    <script src="data/{name}.js"></script>' for name in layer_scripts
    )
    api_script = (
        '    <script src="data/api_projects.js"></script>\n'
        if include_api_projects
        else ""
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>YESAB Static Map Split</title>
  <link rel="stylesheet" href="app.css">
</head>
<body>
  <div class="app">
    <aside class="sidebar">
      <h1>YESAB Project Map</h1>
      <p>Split static viewer for USB or file-share use. Presentation and layer data are separate files, but everything still works over <code>file://</code>.</p>
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
    <script src="data/manifest.js"></script>
{api_script}{scripts}
    <script src="app.js"></script>
</body>
</html>
"""


def site_js() -> str:
    """Return the client-side application code for the split static map site."""
    return """\
(function () {
  const source = window.YESAB_MAP_DATA || {};
  const manifest = source.manifest || { archives: [], bounds: [0, 0, 1, 1], layers: [] };
  const layerMap = source.layers || {};
  const apiProjects = source.apiProjects || {};
  const DATA = {
    archives: manifest.archives || [],
    bounds: manifest.bounds || [0, 0, 1, 1],
    layers: (manifest.layers || []).map((meta) => ({ ...meta, features: layerMap[meta.name] || [] })),
    apiProjects,
    apiSummary: manifest.apiSummary || { available: false, projectCount: 0, matchedProjectCount: 0, fallbackProjectCount: 0, mappedProjectCount: 0, unmappedProjectCount: 0, matchedFeatureCount: 0 },
    sourceInfo: manifest.sourceInfo || {}
  };

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

  const state = {
    visible: new Set(DATA.layers.map((layer) => layer.name)),
    selected: null,
    hovered: null,
    dragging: false,
    lastX: 0,
    lastY: 0,
    scale: 1,
    tx: 0,
    ty: 0
  };

  function esc(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;");
  }

  function listText(items, mapper) {
    if (!items || !items.length) return "";
    return items.map(mapper).filter(Boolean).join(", ");
  }

  function apiUrl(api) {
    const key = (api && (api.projectNumber || api.projectId)) || "";
    return key ? `https://yesabregistry.ca/api/integration/projects/${encodeURIComponent(key)}` : "";
  }

  function registryPageUrl(api) {
    const key = (api && api.projectId) || "";
    return key ? `https://yesabregistry.ca/projects/${encodeURIComponent(key)}` : "";
  }

  function renderApiDetails(api) {
    if (!api) return "";
    const districts = listText(api.assessmentDistricts, (item) => item.name);
    const sectors = listText(api.sectors, (item) => item.name);
    const governments = listText(api.indigenousGovernments, (item) => item);
    const decisionBodies = listText(api.decisionBodies, (item) => item);
    const planning = listText(api.planningCommissions, (item) => item);
    const outcome = api.outcomes && api.outcomes.outcomeName || "";
    const decision = api.outcomes && api.outcomes.decisionName || "";
    const stage = api.stage && api.stage.name || "";
    const daysRemaining = api.stage && api.stage.daysRemaining;
    const stageLabel = stage
      ? `${stage}${Number.isFinite(daysRemaining) ? ` (${daysRemaining} days remaining)` : ""}`
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
      ["Stage History", api.stageHistory && api.stageHistory.length ? `${api.stageHistory.length} entries` : ""],
      ["Locations", api.locations && api.locations.length ? `${api.locations.length} point(s)` : ""]
    ]
      .filter(([, value]) => value)
      .map(([key, value]) => `<dt>${esc(key)}</dt><dd>${esc(value)}</dd>`)
      .join("");
    const summary = api.projectScope && api.projectScope.summary ? `<p>${esc(api.projectScope.summary)}</p>` : "";
    const apiLink = apiUrl(api);
    const pageLink = registryPageUrl(api);
    const links = [
      pageLink ? `<a href="${pageLink}" target="_blank" rel="noreferrer">Registry page</a>` : "",
      apiLink ? `<a href="${apiLink}" target="_blank" rel="noreferrer">API record</a>` : ""
    ].filter(Boolean).join(" | ");
    const linkHtml = links ? `<p>${links}</p>` : "";
    return `
      <h2>Registry</h2>
      ${api.title ? `<p><strong>${esc(api.title)}</strong></p>` : ""}
      <dl>${rows}</dl>
      ${summary}
      ${linkHtml}
    `;
  }

  function renderAbout() {
    const info = DATA.sourceInfo || {};
    const shapefile = info.shapefile || {};
    const registry = info.registry || {};
    const bucketLine = registry.bucketCount
      ? `<dt>API cache coverage</dt><dd>${esc(String(registry.bucketCount))} bucket(s), ${esc(String(registry.projectCount || 0))} projects</dd>`
      : "";
    aboutContent.innerHTML = `
      <h2>About This Map</h2>
      <p>This page combines YESAB project-map shapefile geometry with cached YESAB registry metadata when a project-number match is available, and adds approximate API-only point locations when no shapefile geometry exists.</p>
      <p>This is not a finished product - it is a toy proof-of-concept that happens to have a little usefulness.</p>
      <dl>
        <dt>Page build date</dt><dd>${esc(info.pageBuiltAt || "")}</dd>
        <dt>Map file date</dt><dd>${esc(shapefile.sourceDate || "Unknown")}</dd>
        <dt>Registry cache date</dt><dd>${esc(registry.sourceDate || "Not loaded")}</dd>
        ${bucketLine}
      </dl>
      <p><a href="${esc(shapefile.pageUrl || "#")}" target="_blank" rel="noreferrer">YESAB Project Map File page</a></p>
      <p><a href="${esc(registry.pageUrl || "#")}" target="_blank" rel="noreferrer">YESAB Online Registry</a></p>
    `;
  }

  function setAboutOpen(open) {
    aboutPanel.classList.toggle("open", open);
    aboutPanel.setAttribute("aria-hidden", open ? "false" : "true");
  }

  function resize() {
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    canvas.width = Math.max(1, Math.round(rect.width * dpr));
    canvas.height = Math.max(1, Math.round(rect.height * dpr));
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    render();
  }

  function fitBounds(bounds) {
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
  }

  function worldToScreen(pt) {
    return [pt[0] * state.scale + state.tx, state.ty - pt[1] * state.scale];
  }

  function screenToWorld(x, y) {
    return [(x - state.tx) / state.scale, (state.ty - y) / state.scale];
  }

  function drawFeature(feature, color, selected) {
    const geom = feature.geometry;
    const isHover = state.hovered === feature;
    const hasApi = Boolean(feature.apiProjectNumber);
    const isApiFallback = Boolean(feature.isApiFallback);
    const alpha = selected ? 0.85 : isHover ? 0.72 : 0.48;
    const stroke = selected ? 2.2 : isHover ? 1.7 : 1.1;
    ctx.beginPath();
    if (geom.type === "Point") {
      const [sx, sy] = worldToScreen(geom.coordinates);
      if (isApiFallback) {
        ctx.beginPath();
        ctx.arc(sx, sy, selected ? 7.6 : 6.1, 0, Math.PI * 2);
        ctx.fillStyle = "rgba(15, 118, 110, 0.14)";
        ctx.fill();
        ctx.lineWidth = selected ? 2.3 : 1.7;
        ctx.strokeStyle = "rgba(15, 118, 110, 0.92)";
        ctx.stroke();
        ctx.beginPath();
        ctx.arc(sx, sy, selected ? 2.7 : 2.1, 0, Math.PI * 2);
        ctx.fillStyle = "rgba(255,255,255,0.96)";
        ctx.fill();
        return;
      }
      if (hasApi) {
        ctx.beginPath();
        ctx.arc(sx, sy, selected ? 8.2 : 6.4, 0, Math.PI * 2);
        ctx.fillStyle = "rgba(180, 134, 47, 0.32)";
        ctx.fill();
        ctx.lineWidth = selected ? 2.3 : 1.7;
        ctx.strokeStyle = "rgba(31,41,55,0.78)";
        ctx.stroke();
        ctx.beginPath();
      }
      ctx.arc(sx, sy, selected ? 5 : 3.2, 0, Math.PI * 2);
      ctx.fillStyle = color;
      ctx.globalAlpha = 0.95;
      ctx.fill();
      ctx.globalAlpha = 1;
      if (selected || isHover) {
        ctx.lineWidth = 2;
        ctx.strokeStyle = "rgba(17,24,39,0.85)";
        ctx.stroke();
      }
      return;
    }
    for (const part of geom.coordinates) {
      part.forEach((point, index) => {
        const [sx, sy] = worldToScreen(point);
        if (index === 0) ctx.moveTo(sx, sy);
        else ctx.lineTo(sx, sy);
      });
      if (geom.type === "Polygon" && part.length) {
        const [sx, sy] = worldToScreen(part[0]);
        ctx.lineTo(sx, sy);
      }
    }
    if (geom.type === "Polygon") {
      ctx.fillStyle = color;
      ctx.globalAlpha = alpha * 0.45;
      ctx.fill("evenodd");
      ctx.globalAlpha = 1;
    }
    if (hasApi) {
      ctx.save();
      ctx.lineWidth = stroke + 2.4;
      ctx.strokeStyle = "rgba(180, 134, 47, 0.7)";
      ctx.globalAlpha = selected ? 0.9 : 0.58;
      ctx.stroke();
      ctx.restore();
    }
    ctx.lineWidth = stroke;
    ctx.strokeStyle = color;
    ctx.globalAlpha = alpha;
    ctx.stroke();
    ctx.globalAlpha = 1;
  }

  function drawGrid() {
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
    for (let x = startX; x <= wx1; x += step) {
      const [sx0, sy0] = worldToScreen([x, wy0]);
      const [sx1, sy1] = worldToScreen([x, wy1]);
      ctx.beginPath();
      ctx.moveTo(sx0, sy0);
      ctx.lineTo(sx1, sy1);
      ctx.stroke();
    }
    for (let y = startY; y <= wy1; y += step) {
      const [sx0, sy0] = worldToScreen([wx0, y]);
      const [sx1, sy1] = worldToScreen([wx1, y]);
      ctx.beginPath();
      ctx.moveTo(sx0, sy0);
      ctx.lineTo(sx1, sy1);
      ctx.stroke();
    }
    ctx.restore();
  }

  function render() {
    const rect = canvas.getBoundingClientRect();
    ctx.clearRect(0, 0, rect.width, rect.height);
    const bg = ctx.createLinearGradient(0, 0, rect.width, rect.height);
    bg.addColorStop(0, "#fbf9f3");
    bg.addColorStop(1, "#eee6d5");
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, rect.width, rect.height);
    drawGrid();
    for (const layer of DATA.layers) {
      if (!state.visible.has(layer.name)) continue;
      for (const feature of layer.features) {
        drawFeature(feature, layer.color, state.selected === feature);
      }
    }
  }

  function pointInRing(point, ring) {
    let inside = false;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const xi = ring[i][0], yi = ring[i][1];
      const xj = ring[j][0], yj = ring[j][1];
      const intersect = ((yi > point[1]) !== (yj > point[1])) &&
        (point[0] < ((xj - xi) * (point[1] - yi)) / ((yj - yi) || 1e-9) + xi);
      if (intersect) inside = !inside;
    }
    return inside;
  }

  function distToSegmentSq(p, a, b) {
    const dx = b[0] - a[0];
    const dy = b[1] - a[1];
    if (dx === 0 && dy === 0) {
      const px = p[0] - a[0];
      const py = p[1] - a[1];
      return px * px + py * py;
    }
    const t = Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy)));
    const qx = a[0] + t * dx;
    const qy = a[1] + t * dy;
    const px = p[0] - qx;
    const py = p[1] - qy;
    return px * px + py * py;
  }

  function hitTestFeature(feature, world, tolWorld) {
    const b = feature.bbox;
    if (world[0] < b[0] - tolWorld || world[0] > b[2] + tolWorld || world[1] < b[1] - tolWorld || world[1] > b[3] + tolWorld) {
      return false;
    }
    const geom = feature.geometry;
    if (geom.type === "Point") {
      const dx = world[0] - geom.coordinates[0];
      const dy = world[1] - geom.coordinates[1];
      return dx * dx + dy * dy <= tolWorld * tolWorld;
    }
    if (geom.type === "Polygon") {
      for (const ring of geom.coordinates) {
        if (ring.length > 2 && pointInRing(world, ring)) return true;
      }
    }
    const tolSq = tolWorld * tolWorld;
    for (const part of geom.coordinates) {
      for (let i = 1; i < part.length; i++) {
        if (distToSegmentSq(world, part[i - 1], part[i]) <= tolSq) return true;
      }
    }
    return false;
  }

  function pickFeature(clientX, clientY) {
    const rect = canvas.getBoundingClientRect();
    const x = clientX - rect.left;
    const y = clientY - rect.top;
    const world = screenToWorld(x, y);
    const tolWorld = 8 / state.scale;
    const visibleLayers = DATA.layers.filter((layer) => state.visible.has(layer.name));
    for (let li = visibleLayers.length - 1; li >= 0; li--) {
      const layer = visibleLayers[li];
      for (let i = layer.features.length - 1; i >= 0; i--) {
        const feature = layer.features[i];
        if (hitTestFeature(feature, world, tolWorld)) {
          return { layer, feature };
        }
      }
    }
    return null;
  }

  function updateDetails(selection) {
    if (!selection) {
      detailsEl.innerHTML = '<h2>Selection</h2><p>Click a feature to inspect its attributes.</p>';
      return;
    }
    const rows = Object.entries(selection.feature.properties)
      .map(([key, value]) => `<dt>${esc(key)}</dt><dd>${esc(value)}</dd>`)
      .join("");
    const api = selection.feature.apiProjectNumber ? DATA.apiProjects[selection.feature.apiProjectNumber] : null;
    const approx = selection.feature.isApiFallback
      ? "<p><strong>Approximate API point only.</strong> This project is not present in the shapefile geometry and is shown from registry coordinates.</p>"
      : "";
    detailsEl.innerHTML = `
      <h2>${esc(selection.feature.label)}</h2>
      <p>${esc(selection.layer.name)} from ${esc(selection.layer.archive)}</p>
      ${approx}
      <dl>${rows}</dl>
      ${renderApiDetails(api)}
    `;
  }

  function updateTooltip(pick, clientX, clientY) {
    if (!pick) {
      tooltip.style.display = "none";
      return;
    }
    tooltip.innerHTML = `<strong>${esc(pick.feature.label)}</strong><br>${esc(pick.layer.name)}${pick.feature.isApiFallback ? "<br>Approximate API location" : pick.feature.apiProjectNumber ? "<br>Registry-linked" : ""}`;
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
    tooltip.style.left = `${left}px`;
    tooltip.style.top = `${top}px`;
  }

  function renderLayerList() {
    layersEl.innerHTML = "";
    for (const layer of DATA.layers) {
      const row = document.createElement("label");
      row.className = "layer";
      row.innerHTML = `
        <input type="checkbox" ${state.visible.has(layer.name) ? "checked" : ""}>
        <div>
          <strong>${esc(layer.name)}</strong>
          <span>${layer.count.toLocaleString()} ${esc(layer.type)} features</span>
        </div>
        <div class="swatch" style="background:${layer.color}"></div>
      `;
      row.querySelector("input").addEventListener("change", (event) => {
        if (event.target.checked) state.visible.add(layer.name);
        else state.visible.delete(layer.name);
        render();
      });
      layersEl.appendChild(row);
    }
  }

  function renderMeta() {
    const total = DATA.layers.reduce((sum, layer) => sum + layer.count, 0);
    const apiLine = DATA.apiSummary && DATA.apiSummary.available
      ? `<span>${DATA.apiSummary.matchedProjectCount.toLocaleString()} projects use shapefile geometry; ${DATA.apiSummary.fallbackProjectCount.toLocaleString()} more use approximate API points</span><br><span>${DATA.apiSummary.unmappedProjectCount.toLocaleString()} cached API projects still have no mappable location; ${DATA.apiSummary.matchedFeatureCount.toLocaleString()} geometry features are registry-linked and highlighted in gold</span><br>`
      : '<span>No cached API data loaded</span><br>';
    metaEl.innerHTML = `
      <strong>${total.toLocaleString()} features</strong><br>
      <span>${DATA.layers.length} layers across ${DATA.archives.length} archive(s)</span><br>
      ${apiLine}
      <span>${DATA.archives.map(esc).join(", ")}</span>
    `;
  }

  canvas.addEventListener("mousedown", (event) => {
    state.dragging = true;
    state.lastX = event.clientX;
    state.lastY = event.clientY;
    updateTooltip(null, 0, 0);
  });

  window.addEventListener("mouseup", () => {
    state.dragging = false;
  });

  window.addEventListener("mousemove", (event) => {
    if (state.dragging) {
      state.tx += event.clientX - state.lastX;
      state.ty += event.clientY - state.lastY;
      state.lastX = event.clientX;
      state.lastY = event.clientY;
      state.hovered = null;
      updateTooltip(null, 0, 0);
      render();
    }
  });

  canvas.addEventListener("mousemove", (event) => {
    if (state.dragging) return;
    const pick = pickFeature(event.clientX, event.clientY);
    state.hovered = pick ? pick.feature : null;
    updateTooltip(pick, event.clientX, event.clientY);
    render();
  });

  canvas.addEventListener("mouseleave", () => {
    state.hovered = null;
    updateTooltip(null, 0, 0);
    render();
  });

  aboutLink.addEventListener("click", (event) => {
    event.preventDefault();
    setAboutOpen(!aboutPanel.classList.contains("open"));
  });

  aboutClose.addEventListener("click", () => {
    setAboutOpen(false);
  });

  canvas.addEventListener("click", (event) => {
    const pick = pickFeature(event.clientX, event.clientY);
    state.selected = pick ? pick.feature : null;
    updateDetails(pick);
    render();
  });

  canvas.addEventListener("wheel", (event) => {
    event.preventDefault();
    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    const wx = (x - state.tx) / state.scale;
    const wy = (state.ty - y) / state.scale;
    const factor = Math.exp(-event.deltaY * 0.001);
    state.scale = Math.max(0.00002, Math.min(30, state.scale * factor));
    state.tx = x - wx * state.scale;
    state.ty = y + wy * state.scale;
    render();
  }, { passive: false });

  fitBtn.addEventListener("click", () => fitBounds(DATA.bounds));

  toggleBtn.addEventListener("click", () => {
    if (state.visible.size === DATA.layers.length) state.visible.clear();
    else DATA.layers.forEach((layer) => state.visible.add(layer.name));
    renderLayerList();
    render();
  });

  renderLayerList();
  renderMeta();
  renderAbout();
  resize();
  fitBounds(DATA.bounds);
  window.addEventListener("resize", resize);
})();
"""


def write_data_files(payload: dict[str, object], output_data_dir: Path) -> list[str]:
    """Write the manifest and one JavaScript data file per map layer."""
    manifest = {
        "archives": payload["archives"],
        "bounds": payload["bounds"],
        "apiSummary": payload["apiSummary"],
        "sourceInfo": payload["sourceInfo"],
        "layers": [
            {
                "name": layer["name"],
                "archive": layer["archive"],
                "color": layer["color"],
                "type": layer["type"],
                "count": layer["count"],
            }
            for layer in payload["layers"]
        ],
    }
    manifest_js = (
        "window.YESAB_MAP_DATA = window.YESAB_MAP_DATA || {}; window.YESAB_MAP_DATA.manifest = "
        + json.dumps(manifest, separators=(",", ":"))
        + ";\n"
    )
    (output_data_dir / "manifest.js").write_text(manifest_js, encoding="utf-8")

    if payload["apiProjects"]:
        api_js = (
            "window.YESAB_MAP_DATA = window.YESAB_MAP_DATA || {}; "
            "window.YESAB_MAP_DATA.apiProjects = "
            + json.dumps(payload["apiProjects"], separators=(",", ":"))
            + ";\n"
        )
        (output_data_dir / "api_projects.js").write_text(api_js, encoding="utf-8")

    layer_names: list[str] = []
    for layer in payload["layers"]:
        name = layer["name"]
        layer_names.append(name)
        layer_js = (
            "window.YESAB_MAP_DATA = window.YESAB_MAP_DATA || {}; "
            "window.YESAB_MAP_DATA.layers = window.YESAB_MAP_DATA.layers || {}; "
            f'window.YESAB_MAP_DATA.layers["{name}"] = '
            + json.dumps(layer["features"], separators=(",", ":"))
            + ";\n"
        )
        (output_data_dir / f"{name}.js").write_text(layer_js, encoding="utf-8")
    return layer_names


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for the output directory."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "output_dir",
        nargs="?",
        default=DEFAULT_OUTPUT_DIR,
        type=Path,
        help="Directory to write the generated site into (default: ./out).",
    )
    return parser.parse_args()


def build_site(output_dir: Path) -> None:
    """Generate the full split-site output directory."""
    payload = load_payload()
    output_data_dir = output_dir / "data"
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_data_dir.mkdir(parents=True, exist_ok=True)

    layer_names = write_data_files(payload, output_data_dir)
    (output_dir / "index.html").write_text(
        site_html(layer_names, include_api_projects=bool(payload["apiProjects"])),
        encoding="utf-8",
    )
    (output_dir / "app.css").write_text(site_css(), encoding="utf-8")
    (output_dir / "app.js").write_text(site_js(), encoding="utf-8")
    (output_dir / "qa_report.json").write_text(
        json.dumps(payload["qa"], indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (output_dir / "qa_report.html").write_text(
        build_qa_html(payload["qa"], "YESAB API Match QA"),
        encoding="utf-8",
    )

    total = sum(layer["count"] for layer in payload["layers"])
    print(
        f"Wrote {output_dir} with {len(payload['layers'])} layers and {total} features."
    )
    print("Wrote QA artifacts: qa_report.html, qa_report.json")


if __name__ == "__main__":
    build_site(parse_args().output_dir)
