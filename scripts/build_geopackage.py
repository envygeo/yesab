"""Build a GeoPackage from the enriched YESAB map payload.

The output keeps the existing map layer split while exposing the joined
registry attributes as GIS-friendly feature tables.

(c)2026 Matt Wilkie, Yukon Government. MIT License.
"""

# /// script
# requires-python = ">=3.14"
# ///
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import struct
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_static_map_single import load_layers


DEFAULT_OUTPUT_PATH = Path("./out/yesab-projects.gpkg")
GEOMETRY_COLUMN = "geom"
YUKON_ALBERS_SRS_ID = 3578
YUKON_ALBERS_WKT = (
    'PROJCS["NAD_1983_Yukon_Albers",'
    'GEOGCS["GCS_North_American_1983",'
    'DATUM["D_North_American_1983",'
    'SPHEROID["GRS_1980",6378137.0,298.257222101]],'
    'PRIMEM["Greenwich",0.0],'
    'UNIT["Degree",0.0174532925199433]],'
    'PROJECTION["Albers"],'
    'PARAMETER["False_Easting",500000.0],'
    'PARAMETER["False_Northing",500000.0],'
    'PARAMETER["Central_Meridian",-132.5],'
    'PARAMETER["Standard_Parallel_1",61.66666666666666],'
    'PARAMETER["Standard_Parallel_2",68.0],'
    'PARAMETER["Latitude_Of_Origin",59.0],'
    'UNIT["Meter",1.0]]'
)

COMMON_FIELDS = (
    "layer_name",
    "source_archive",
    "source_feature_id",
    "feature_label",
    "geometry_source",
    "api_join_status",
    "api_project_number",
)

REGISTRY_FIELDS = (
    "registry_project_number",
    "registry_project_id",
    "registry_title",
    "registry_type",
    "registry_proponent",
    "registry_stage",
    "registry_days_remaining",
    "registry_districts",
    "registry_sectors",
    "registry_indigenous_governments",
    "registry_decision_bodies",
    "registry_planning_commissions",
    "registry_outcome",
    "registry_decision",
    "registry_location_count",
    "registry_scope_summary",
    "registry_page_url",
    "registry_api_url",
)


def quote_identifier(identifier: str) -> str:
    """Return a SQLite quoted identifier."""
    return '"' + identifier.replace('"', '""') + '"'


def safe_identifier(value: str, fallback: str) -> str:
    """Make a stable SQLite/GIS field or table name from arbitrary source text."""
    cleaned = re.sub(r"[^0-9A-Za-z_]+", "_", value.strip()).strip("_").lower()
    if not cleaned:
        cleaned = fallback
    if cleaned[0].isdigit():
        cleaned = f"_{cleaned}"
    return cleaned[:58]


def unique_name(base: str, used: set[str]) -> str:
    """Return a field name that is unique in ``used``."""
    candidate = base
    index = 2
    while candidate in used:
        suffix = f"_{index}"
        candidate = f"{base[: 58 - len(suffix)]}{suffix}"
        index += 1
    used.add(candidate)
    return candidate


def text_value(value: Any) -> str:
    """Flatten values into strings suitable for portable GeoPackage attributes."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def repo_output_path(output_path: Path) -> Path:
    """Resolve relative output paths under the repository root."""
    if output_path.is_absolute():
        return output_path
    return ROOT / output_path


def names_from_items(items: Any) -> str:
    """Flatten registry name lists into comma-separated text."""
    if not isinstance(items, list):
        return ""
    names: list[str] = []
    for item in items:
        if isinstance(item, dict):
            name = text_value(item.get("name")).strip()
        else:
            name = text_value(item).strip()
        if name:
            names.append(name)
    return ", ".join(names)


def registry_url(project_id: str) -> str:
    """Return the public registry page for an API project ID."""
    if not project_id:
        return ""
    return f"https://yesabregistry.ca/projects/{project_id}"


def api_url(project_number_or_id: str) -> str:
    """Return the YESAB registry API URL for a project."""
    if not project_number_or_id:
        return ""
    return f"https://yesabregistry.ca/api/integration/projects/{project_number_or_id}"


def registry_attrs(project: dict[str, Any] | None) -> dict[str, str]:
    """Return flattened registry attributes for a joined API project."""
    if not project:
        return {field: "" for field in REGISTRY_FIELDS}
    stage = project.get("stage") if isinstance(project.get("stage"), dict) else {}
    outcomes = (
        project.get("outcomes") if isinstance(project.get("outcomes"), dict) else {}
    )
    scope = (
        project.get("projectScope")
        if isinstance(project.get("projectScope"), dict)
        else {}
    )
    project_number = text_value(project.get("projectNumber"))
    project_id = text_value(project.get("projectId"))
    return {
        "registry_project_number": project_number,
        "registry_project_id": project_id,
        "registry_title": text_value(project.get("title")),
        "registry_type": text_value(project.get("projectTypeName")),
        "registry_proponent": text_value(project.get("proponentName")),
        "registry_stage": text_value(stage.get("name")),
        "registry_days_remaining": text_value(stage.get("daysRemaining")),
        "registry_districts": names_from_items(project.get("assessmentDistricts")),
        "registry_sectors": names_from_items(project.get("sectors")),
        "registry_indigenous_governments": names_from_items(
            project.get("indigenousGovernments")
        ),
        "registry_decision_bodies": names_from_items(project.get("decisionBodies")),
        "registry_planning_commissions": names_from_items(
            project.get("planningCommissions")
        ),
        "registry_outcome": text_value(outcomes.get("outcomeName")),
        "registry_decision": text_value(outcomes.get("decisionName")),
        "registry_location_count": text_value(len(project.get("locations", []))),
        "registry_scope_summary": text_value(scope.get("summary")),
        "registry_page_url": registry_url(project_id),
        "registry_api_url": api_url(project_number or project_id),
    }


def gpkg_header(wkb: bytes, srs_id: int = YUKON_ALBERS_SRS_ID) -> bytes:
    """Wrap WKB in a GeoPackage binary geometry header."""
    return b"GP" + struct.pack("<BBi", 0, 1, srs_id) + wkb


def wkb_point(coordinates: list[float]) -> bytes:
    return struct.pack("<BIdd", 1, 1, coordinates[0], coordinates[1])


def wkb_linestring(points: list[list[float]]) -> bytes:
    payload = struct.pack("<BIi", 1, 2, len(points))
    payload += b"".join(struct.pack("<dd", point[0], point[1]) for point in points)
    return payload


def wkb_polygon(rings: list[list[list[float]]]) -> bytes:
    closed_rings: list[list[list[float]]] = []
    for ring in rings:
        if not ring:
            continue
        closed = list(ring)
        if closed[0] != closed[-1]:
            closed.append(closed[0])
        closed_rings.append(closed)
    payload = struct.pack("<BIi", 1, 3, len(closed_rings))
    for ring in closed_rings:
        payload += struct.pack("<i", len(ring))
        payload += b"".join(struct.pack("<dd", point[0], point[1]) for point in ring)
    return payload


def wkb_multilinestring(parts: list[list[list[float]]]) -> bytes:
    payload = struct.pack("<BIi", 1, 5, len(parts))
    payload += b"".join(wkb_linestring(part) for part in parts)
    return payload


def gpkg_geometry(geometry: dict[str, Any]) -> bytes:
    """Convert a map-payload geometry to a GeoPackage binary geometry."""
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if geometry_type == "Point":
        return gpkg_header(wkb_point(coordinates))
    if geometry_type == "LineString":
        return gpkg_header(wkb_multilinestring(coordinates or []))
    if geometry_type == "Polygon":
        return gpkg_header(wkb_polygon(coordinates or []))
    raise ValueError(f"Unsupported geometry type: {geometry_type}")


def gpkg_geometry_type_name(layer: dict[str, Any]) -> str:
    """Return the GeoPackage metadata geometry type for a payload layer."""
    geometry_type = layer.get("type")
    if geometry_type == "Point":
        return "POINT"
    if geometry_type == "LineString":
        return "MULTILINESTRING"
    if geometry_type == "Polygon":
        return "POLYGON"
    return "GEOMETRY"


def create_core_tables(db: sqlite3.Connection) -> None:
    """Create the required GeoPackage metadata tables."""
    db.executescript(
        """
        PRAGMA application_id = 1196437808;
        PRAGMA user_version = 10400;

        CREATE TABLE gpkg_spatial_ref_sys (
          srs_name TEXT NOT NULL,
          srs_id INTEGER NOT NULL PRIMARY KEY,
          organization TEXT NOT NULL,
          organization_coordsys_id INTEGER NOT NULL,
          definition TEXT NOT NULL,
          description TEXT
        );

        CREATE TABLE gpkg_contents (
          table_name TEXT NOT NULL PRIMARY KEY,
          data_type TEXT NOT NULL,
          identifier TEXT UNIQUE,
          description TEXT DEFAULT '',
          last_change DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
          min_x DOUBLE,
          min_y DOUBLE,
          max_x DOUBLE,
          max_y DOUBLE,
          srs_id INTEGER,
          CONSTRAINT fk_gc_r_srs_id FOREIGN KEY (srs_id)
            REFERENCES gpkg_spatial_ref_sys(srs_id)
        );

        CREATE TABLE gpkg_geometry_columns (
          table_name TEXT NOT NULL,
          column_name TEXT NOT NULL,
          geometry_type_name TEXT NOT NULL,
          srs_id INTEGER NOT NULL,
          z TINYINT NOT NULL,
          m TINYINT NOT NULL,
          PRIMARY KEY (table_name, column_name),
          CONSTRAINT fk_gc_tn FOREIGN KEY (table_name)
            REFERENCES gpkg_contents(table_name),
          CONSTRAINT fk_gc_srs FOREIGN KEY (srs_id)
            REFERENCES gpkg_spatial_ref_sys(srs_id)
        );
        """
    )
    db.executemany(
        """
        INSERT INTO gpkg_spatial_ref_sys
          (srs_name, srs_id, organization, organization_coordsys_id, definition, description)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            ("Undefined cartesian SRS", -1, "NONE", -1, "undefined", "undefined"),
            ("Undefined geographic SRS", 0, "NONE", 0, "undefined", "undefined"),
            (
                "WGS 84 geodetic",
                4326,
                "EPSG",
                4326,
                'GEOGCS["WGS 84",DATUM["WGS_1984",'
                'SPHEROID["WGS 84",6378137,298.257223563]],'
                'PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]]',
                "longitude/latitude coordinates in decimal degrees on WGS 84",
            ),
            (
                "NAD83 / Yukon Albers",
                YUKON_ALBERS_SRS_ID,
                "EPSG",
                YUKON_ALBERS_SRS_ID,
                YUKON_ALBERS_WKT,
                "YESAB source geometry CRS",
            ),
        ],
    )


def layer_columns(layer: dict[str, Any]) -> tuple[list[str], dict[str, str]]:
    """Return physical column names and source-property mapping for a feature layer."""
    used = {"fid", GEOMETRY_COLUMN}
    columns = [unique_name(field, used) for field in COMMON_FIELDS + REGISTRY_FIELDS]
    property_columns: dict[str, str] = {}
    for feature in layer.get("features", []):
        props = feature.get("properties", {})
        if not isinstance(props, dict):
            continue
        for key in props:
            if key not in property_columns:
                property_columns[key] = unique_name(
                    safe_identifier(f"attr_{key}", "attr"), used
                )
    columns.extend(property_columns.values())
    return columns, property_columns


def row_for_feature(
    layer: dict[str, Any],
    feature: dict[str, Any],
    property_columns: dict[str, str],
    api_projects: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Build one table row from a map-payload feature."""
    api_project_number = text_value(feature.get("apiProjectNumber"))
    is_fallback = bool(feature.get("isApiFallback"))
    row = {
        "layer_name": text_value(layer.get("name")),
        "source_archive": text_value(layer.get("archive")),
        "source_feature_id": text_value(feature.get("id")),
        "feature_label": text_value(feature.get("label")),
        "geometry_source": "api_approximate_point" if is_fallback else "yesab_shapefile",
        "api_join_status": "api_fallback" if is_fallback else "matched" if api_project_number else "unmatched",
        "api_project_number": api_project_number,
    }
    row.update(registry_attrs(api_projects.get(api_project_number)))
    props = feature.get("properties", {})
    if isinstance(props, dict):
        for key, column in property_columns.items():
            row[column] = text_value(props.get(key))
    return row


def write_feature_layer(
    db: sqlite3.Connection,
    layer: dict[str, Any],
    api_projects: dict[str, dict[str, Any]],
    last_change: str,
) -> int:
    """Write one map payload layer as a GeoPackage feature table."""
    table_name = safe_identifier(text_value(layer.get("name")), "yesab_layer")
    columns, property_columns = layer_columns(layer)
    db.execute(
        f"""
        CREATE TABLE {quote_identifier(table_name)} (
          fid INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
          {quote_identifier(GEOMETRY_COLUMN)} BLOB NOT NULL,
          {", ".join(f"{quote_identifier(column)} TEXT" for column in columns)}
        )
        """
    )
    db.execute(
        """
        INSERT INTO gpkg_contents
          (table_name, data_type, identifier, description, last_change, min_x, min_y, max_x, max_y, srs_id)
        VALUES (?, 'features', ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            table_name,
            text_value(layer.get("name")),
            f"YESAB enriched feature layer from {text_value(layer.get('archive'))}",
            last_change,
            None,
            None,
            None,
            None,
            YUKON_ALBERS_SRS_ID,
        ),
    )
    db.execute(
        """
        INSERT INTO gpkg_geometry_columns
          (table_name, column_name, geometry_type_name, srs_id, z, m)
        VALUES (?, ?, ?, ?, 0, 0)
        """,
        (
            table_name,
            GEOMETRY_COLUMN,
            gpkg_geometry_type_name(layer),
            YUKON_ALBERS_SRS_ID,
        ),
    )

    insert_columns = [GEOMETRY_COLUMN, *columns]
    placeholders = ", ".join("?" for _ in insert_columns)
    sql = (
        f"INSERT INTO {quote_identifier(table_name)} "
        f"({', '.join(quote_identifier(column) for column in insert_columns)}) "
        f"VALUES ({placeholders})"
    )
    count = 0
    bounds: list[float] | None = None
    for feature in layer.get("features", []):
        geometry = feature.get("geometry")
        if not isinstance(geometry, dict):
            continue
        row = row_for_feature(layer, feature, property_columns, api_projects)
        db.execute(sql, [gpkg_geometry(geometry), *[row.get(column, "") for column in columns]])
        count += 1
        bbox = feature.get("bbox")
        if isinstance(bbox, list) and len(bbox) == 4:
            if bounds is None:
                bounds = [float(item) for item in bbox]
            else:
                bounds[0] = min(bounds[0], float(bbox[0]))
                bounds[1] = min(bounds[1], float(bbox[1]))
                bounds[2] = max(bounds[2], float(bbox[2]))
                bounds[3] = max(bounds[3], float(bbox[3]))
    if bounds is not None:
        db.execute(
            """
            UPDATE gpkg_contents
               SET min_x = ?, min_y = ?, max_x = ?, max_y = ?
             WHERE table_name = ?
            """,
            (*bounds, table_name),
        )
    return count


def write_summary_tables(
    db: sqlite3.Connection, payload: dict[str, Any], last_change: str
) -> None:
    """Write non-spatial source and QA summary tables into the GeoPackage."""
    db.execute(
        """
        CREATE TABLE yesab_export_summary (
          id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
          item TEXT,
          value TEXT
        )
        """
    )
    db.execute(
        """
        INSERT INTO gpkg_contents
          (table_name, data_type, identifier, description, last_change)
        VALUES ('yesab_export_summary', 'attributes', 'yesab_export_summary',
          'YESAB export source and QA summary', ?)
        """,
        (last_change,),
    )
    summary = payload.get("apiSummary", {})
    source_info = payload.get("sourceInfo", {})
    rows = [
        ("built_at_utc", last_change),
        ("archives", text_value(payload.get("archives", []))),
        ("source_info", text_value(source_info)),
        ("api_summary", text_value(summary)),
        ("qa_summary", text_value(payload.get("qa", {}).get("summary", {}))),
    ]
    db.executemany(
        "INSERT INTO yesab_export_summary (item, value) VALUES (?, ?)",
        rows,
    )

    db.execute(
        """
        CREATE TABLE yesab_unmapped_api_projects (
          id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
          project_number TEXT,
          project_id TEXT,
          title TEXT,
          project_type TEXT,
          proponent TEXT,
          stage TEXT,
          districts TEXT,
          sectors TEXT,
          location_count TEXT,
          api_url TEXT
        )
        """
    )
    db.execute(
        """
        INSERT INTO gpkg_contents
          (table_name, data_type, identifier, description, last_change)
        VALUES ('yesab_unmapped_api_projects', 'attributes',
          'yesab_unmapped_api_projects',
          'Cached YESAB API projects without shapefile geometry or usable fallback coordinates',
          ?)
        """,
        (last_change,),
    )
    unmapped = payload.get("qa", {}).get("unmappedApiProjects", [])
    db.executemany(
        """
        INSERT INTO yesab_unmapped_api_projects
          (project_number, project_id, title, project_type, proponent, stage,
           districts, sectors, location_count, api_url)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                text_value(item.get("projectNumber")),
                text_value(item.get("projectId")),
                text_value(item.get("title")),
                text_value(item.get("projectTypeName")),
                text_value(item.get("proponentName")),
                text_value(item.get("stageName")),
                text_value(item.get("districts", [])),
                text_value(item.get("sectors", [])),
                text_value(item.get("locationCount")),
                api_url(text_value(item.get("projectNumber"))),
            )
            for item in unmapped
            if isinstance(item, dict)
        ],
    )


def write_geopackage(output_path: Path) -> dict[str, int]:
    """Build and write the enriched YESAB GeoPackage."""
    output_path = repo_output_path(output_path)
    payload = load_layers()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()
    counts: dict[str, int] = {}
    last_change = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    with sqlite3.connect(output_path) as db:
        create_core_tables(db)
        api_projects = payload.get("apiProjects", {})
        if not isinstance(api_projects, dict):
            api_projects = {}
        for layer in payload.get("layers", []):
            if not isinstance(layer, dict):
                continue
            layer_name = text_value(layer.get("name"))
            counts[layer_name] = write_feature_layer(
                db, layer, api_projects, last_change
            )
        write_summary_tables(db, payload, last_change)
        db.commit()
        db.execute("VACUUM")
    return counts


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "output",
        nargs="?",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help=f"GeoPackage output path (default: {DEFAULT_OUTPUT_PATH})",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_path = repo_output_path(args.output)
    counts = write_geopackage(output_path)
    print(f"Wrote {output_path}")
    for layer_name, count in counts.items():
        print(f"  {layer_name}: {count} features")


if __name__ == "__main__":
    main()
