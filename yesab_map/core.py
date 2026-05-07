"""Shared data-shaping helpers for the YESAB static map builders."""

from __future__ import annotations

import csv
import json
import math
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import compression.zstd as zstd

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
API_CACHE_FILE = DATA_DIR / "api" / "projects_merged.json.zst"
API_STATE_FILE = DATA_DIR / "api" / "state.json"
API_LOCATION_OVERRIDES_FILE = DATA_DIR / "api" / "location_overrides.csv"
ZIP_STATE_FILE = DATA_DIR / "yesab_all_zip.state.json"
PROJECT_MAP_PAGE_URL = "https://yesab.ca/project-map"
PROJECT_MAP_ARCHIVE_URL = (
    "https://yesab.ca/wp-content/plugins/yesab-map-wp-plugin/geojson/all.zip"
)
REGISTRY_FRONT_URL = "https://yesabregistry.ca/"
REGISTRY_API_URL = "https://yesabregistry.ca/api/integration/projects"
YST = timezone(timedelta(hours=-7), name="YST")

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

LOCAL_IMPORT_CSS = """
.local-import {
  display: grid;
  gap: 8px;
  margin: 0 0 16px;
}
.local-import label {
  color: var(--muted);
  font-size: 0.82rem;
}
.local-import input {
  width: 100%;
  color: var(--accent);
  font: inherit;
  font-size: 0.84rem;
}
.local-import-status {
  min-height: 1.1em;
  color: var(--muted);
  font-size: 0.78rem;
}
"""

LOCAL_IMPORT_HTML = """
      <div class="local-import">
        <label for="localFileInput">Add Local Layer</label>
        <input id="localFileInput" type="file" accept=".kml,.shp,.dbf" multiple>
        <span class="local-import-status" id="localImportStatus"></span>
      </div>
"""

LOCAL_IMPORT_JS = r"""
  const localFileInput = document.getElementById("localFileInput");
  const localImportStatus = document.getElementById("localImportStatus");

  const LOCAL_LAYER_COLOR = "#2563eb";

  function readAscii(view, offset, length) {
    let text = "";
    for (let i = 0; i < length; i += 1) {
      const code = view.getUint8(offset + i);
      if (code === 0) break;
      text += String.fromCharCode(code);
    }
    return text.trim();
  }

  function decodeDbfText(bytes) {
    return Array.from(bytes, (byte) => String.fromCharCode(byte)).join("").trim();
  }

  function readDbf(buffer) {
    const view = new DataView(buffer);
    const recordCount = view.getUint32(4, true);
    const headerLength = view.getUint16(8, true);
    const recordLength = view.getUint16(10, true);
    const fields = [];
    let offset = 32;
    while (offset < headerLength && view.getUint8(offset) !== 0x0d) {
      fields.push({
        name: readAscii(view, offset, 11),
        type: String.fromCharCode(view.getUint8(offset + 11)),
        length: view.getUint8(offset + 16)
      });
      offset += 32;
    }
    const records = [];
    let pos = headerLength;
    for (let i = 0; i < recordCount && pos + recordLength <= view.byteLength; i += 1) {
      if (view.getUint8(pos) === 0x2a) {
        pos += recordLength;
        continue;
      }
      const row = {};
      let cursor = pos + 1;
      for (const field of fields) {
        const bytes = new Uint8Array(buffer, cursor, field.length);
        const value = decodeDbfText(bytes);
        if (value) row[field.name] = value;
        cursor += field.length;
      }
      records.push(row);
      pos += recordLength;
    }
    return records;
  }

  function roundLocalCoord(value) {
    return Math.round(value * 10) / 10;
  }

  function readShp(buffer) {
    const view = new DataView(buffer);
    const features = [];
    let pos = 100;
    while (pos + 8 <= view.byteLength) {
      const contentLength = view.getInt32(pos + 4, false) * 2;
      const recOffset = pos + 8;
      pos += 8 + contentLength;
      if (recOffset + 4 > view.byteLength) continue;
      const shapeType = view.getInt32(recOffset, true);
      if (shapeType === 0) continue;
      if (shapeType === 1) {
        const x = roundLocalCoord(view.getFloat64(recOffset + 4, true));
        const y = roundLocalCoord(view.getFloat64(recOffset + 12, true));
        features.push({
          geometry: { type: "Point", coordinates: [x, y] },
          bbox: [x, y, x, y]
        });
        continue;
      }
      if (![3, 5].includes(shapeType)) {
        throw new Error(`Unsupported shapefile geometry type ${shapeType}`);
      }
      const xmin = roundLocalCoord(view.getFloat64(recOffset + 4, true));
      const ymin = roundLocalCoord(view.getFloat64(recOffset + 12, true));
      const xmax = roundLocalCoord(view.getFloat64(recOffset + 20, true));
      const ymax = roundLocalCoord(view.getFloat64(recOffset + 28, true));
      const numParts = view.getInt32(recOffset + 36, true);
      const numPoints = view.getInt32(recOffset + 40, true);
      const parts = [];
      for (let i = 0; i < numParts; i += 1) {
        parts.push(view.getInt32(recOffset + 44 + i * 4, true));
      }
      const pointOffset = recOffset + 44 + numParts * 4;
      const points = [];
      for (let i = 0; i < numPoints; i += 1) {
        const pointPos = pointOffset + i * 16;
        points.push([
          roundLocalCoord(view.getFloat64(pointPos, true)),
          roundLocalCoord(view.getFloat64(pointPos + 8, true))
        ]);
      }
      const coordinates = parts.map((start, index) => {
        const end = index + 1 < parts.length ? parts[index + 1] : points.length;
        return points.slice(start, end);
      }).filter((part) => part.length);
      features.push({
        geometry: { type: shapeType === 3 ? "LineString" : "Polygon", coordinates },
        bbox: [xmin, ymin, xmax, ymax]
      });
    }
    return features;
  }

  function projectLonLatToYukonAlbers(longitude, latitude) {
    const grs80A = 6378137.0;
    const flattening = 1 / 298.257222101;
    const eccentricity = Math.sqrt(2 * flattening - flattening * flattening);
    const centralMeridian = -132.5 * Math.PI / 180;
    const standardParallel1 = 61.66666666666666 * Math.PI / 180;
    const standardParallel2 = 68.0 * Math.PI / 180;
    const latitudeOfOrigin = 59.0 * Math.PI / 180;
    function albersQ(phi) {
      const sinPhi = Math.sin(phi);
      const eSinPhi = eccentricity * sinPhi;
      return (1 - eccentricity ** 2) * (
        sinPhi / (1 - eSinPhi * eSinPhi) -
        (1 / (2 * eccentricity)) * Math.log((1 - eSinPhi) / (1 + eSinPhi))
      );
    }
    function albersM(phi) {
      const sinPhi = Math.sin(phi);
      return Math.cos(phi) / Math.sqrt(1 - eccentricity ** 2 * sinPhi * sinPhi);
    }
    const m1 = albersM(standardParallel1);
    const m2 = albersM(standardParallel2);
    const q0 = albersQ(latitudeOfOrigin);
    const q1 = albersQ(standardParallel1);
    const q2 = albersQ(standardParallel2);
    const q = albersQ(latitude * Math.PI / 180);
    const n = (m1 * m1 - m2 * m2) / (q2 - q1);
    const c = m1 * m1 + n * q1;
    const rho0 = grs80A * Math.sqrt(c - n * q0) / n;
    const rho = grs80A * Math.sqrt(Math.max(0, c - n * q)) / n;
    const theta = n * (longitude * Math.PI / 180 - centralMeridian);
    return [
      roundLocalCoord(500000 + rho * Math.sin(theta)),
      roundLocalCoord(500000 + rho0 - rho * Math.cos(theta))
    ];
  }

  function boundsForGeometry(geometry) {
    const points = [];
    if (geometry.type === "Point") points.push(geometry.coordinates);
    else geometry.coordinates.forEach((part) => part.forEach((point) => points.push(point)));
    return points.reduce((bounds, point) => [
      Math.min(bounds[0], point[0]),
      Math.min(bounds[1], point[1]),
      Math.max(bounds[2], point[0]),
      Math.max(bounds[3], point[1])
    ], [Infinity, Infinity, -Infinity, -Infinity]);
  }

  function parseKmlCoordinates(text) {
    return text.trim().split(/\s+/).map((item) => {
      const [longitude, latitude] = item.split(",").map(Number);
      if (!Number.isFinite(longitude) || !Number.isFinite(latitude)) return null;
      return projectLonLatToYukonAlbers(longitude, latitude);
    }).filter(Boolean);
  }

  function parseKmlGeometry(node) {
    const tag = node.localName;
    if (tag === "Point") {
      const coords = node.getElementsByTagName("coordinates")[0]?.textContent || "";
      const point = parseKmlCoordinates(coords)[0];
      return point ? { type: "Point", coordinates: point } : null;
    }
    if (tag === "LineString") {
      const coords = node.getElementsByTagName("coordinates")[0]?.textContent || "";
      const line = parseKmlCoordinates(coords);
      return line.length ? { type: "LineString", coordinates: [line] } : null;
    }
    if (tag === "Polygon") {
      const rings = Array.from(node.getElementsByTagName("LinearRing"))
        .map((ring) => parseKmlCoordinates(ring.getElementsByTagName("coordinates")[0]?.textContent || ""))
        .filter((ring) => ring.length);
      return rings.length ? { type: "Polygon", coordinates: rings } : null;
    }
    return null;
  }

  function parseKmlDocument(text, fileName) {
    const doc = new DOMParser().parseFromString(text, "application/xml");
    const parserError = doc.getElementsByTagName("parsererror")[0];
    if (parserError) throw new Error("KML could not be parsed.");
    const features = [];
    const placemarks = Array.from(doc.getElementsByTagName("Placemark"));
    placemarks.forEach((placemark, index) => {
      const name = placemark.getElementsByTagName("name")[0]?.textContent?.trim() || `${fileName} #${index + 1}`;
      const candidates = ["Point", "LineString", "Polygon"].flatMap((tag) => Array.from(placemark.getElementsByTagName(tag)));
      candidates.forEach((node) => {
        const geometry = parseKmlGeometry(node);
        if (!geometry) return;
        features.push({
          id: features.length + 1,
          label: name,
          bbox: boundsForGeometry(geometry),
          properties: { Name: name, Source: fileName },
          geometry,
          apiProjectNumber: ""
        });
      });
    });
    return features;
  }

  function localLayerType(features) {
    const types = new Set(features.map((feature) => feature.geometry.type));
    return types.size === 1 ? Array.from(types)[0] : "Mixed";
  }

  function addLocalLayer(name, features) {
    if (!features.length) throw new Error(`${name} did not contain supported features.`);
    const layerBounds = features.reduce((bounds, feature) => [
      Math.min(bounds[0], feature.bbox[0]),
      Math.min(bounds[1], feature.bbox[1]),
      Math.max(bounds[2], feature.bbox[2]),
      Math.max(bounds[3], feature.bbox[3])
    ], [Infinity, Infinity, -Infinity, -Infinity]);
    const layer = {
      name,
      archive: "local device",
      color: LOCAL_LAYER_COLOR,
      type: localLayerType(features),
      count: features.length,
      features
    };
    DATA.layers.push(layer);
    if (!DATA.archives.includes("local device")) DATA.archives.push("local device");
    DATA.bounds = [
      Math.min(DATA.bounds[0], layerBounds[0]),
      Math.min(DATA.bounds[1], layerBounds[1]),
      Math.max(DATA.bounds[2], layerBounds[2]),
      Math.max(DATA.bounds[3], layerBounds[3])
    ];
    state.visible.add(layer.name);
    renderLayerList();
    renderMeta();
    fitBounds(DATA.bounds);
    render();
  }

  async function importLocalFiles(fileList) {
    const files = Array.from(fileList || []);
    if (!files.length) return;
    localImportStatus.textContent = "Reading local files...";
    let imported = 0;
    const byLowerName = new Map(files.map((file) => [file.name.toLowerCase(), file]));
    for (const file of files) {
      const lower = file.name.toLowerCase();
      if (lower.endsWith(".kml")) {
        const features = parseKmlDocument(await file.text(), file.name);
        addLocalLayer(file.name.replace(/\.kml$/i, ""), features);
        imported += 1;
      }
      if (lower.endsWith(".shp")) {
        const stem = file.name.replace(/\.shp$/i, "");
        const dbf = byLowerName.get(`${stem.toLowerCase()}.dbf`);
        const geoms = readShp(await file.arrayBuffer());
        const records = dbf ? readDbf(await dbf.arrayBuffer()) : [];
        const features = geoms.map((geom, index) => {
          const properties = records[index] || {};
          const label = properties.Prj_Name || properties.PROPERTY_N || properties.ProjectID || properties.Prj_ID || properties.YESAB_PROJ || properties.Number || `${stem} #${index + 1}`;
          return {
            id: index + 1,
            label,
            bbox: geom.bbox,
            properties,
            geometry: geom.geometry,
            apiProjectNumber: ""
          };
        });
        addLocalLayer(stem, features);
        imported += 1;
      }
    }
    localImportStatus.textContent = imported ? `Loaded ${imported} local layer(s).` : "Choose KML or SHP files.";
  }
"""


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
