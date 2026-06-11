"""Build a query-friendly SQLite database for offline YESAB exploration.

The generated database is intended to be served with Datasette. It keeps the
existing static map and GeoPackage outputs intact while adding a relational,
searchable view of cached YESAB projects, map join status, and locally
downloaded project bundle manifests.

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
import sys
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


DEFAULT_OUTPUT_PATH = Path("./out/yesab-explorer.db")
DEFAULT_API_CACHE_PATH = Path("./data/api/projects_merged.json.zst")
DEFAULT_BUNDLE_ROOT = Path("./out/project-bundles")

PROJECT_NUMBER_FIELDS = ("ProjectID", "Prj_ID", "YESAB_PROJ", "Number")


def repo_path(path: Path) -> Path:
    """Resolve relative paths under the repository root."""
    if path.is_absolute():
        return path
    return ROOT / path


def now_utc() -> str:
    """Return a stable UTC timestamp string."""
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def text_value(value: Any) -> str:
    """Return a compact string representation for SQLite text columns."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def int_value(value: Any) -> int | None:
    """Return an integer value when ``value`` is integer-like."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def float_value(value: Any) -> float | None:
    """Return a float value when ``value`` is numeric."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def bool_int(value: Any) -> int:
    """Return SQLite-friendly boolean integer values."""
    return 1 if value is True else 0


def project_year(project_number: str) -> int | None:
    """Extract the year prefix from a YESAB project number."""
    match = re.match(r"^(\d{4})-", project_number)
    if not match:
        return None
    return int(match.group(1))


def registry_page_url(project_id: str) -> str:
    """Return the public registry project URL."""
    if not project_id:
        return ""
    return f"https://yesabregistry.ca/projects/{project_id}"


def registry_api_url(project_number_or_id: str) -> str:
    """Return the public registry API URL for a project identifier."""
    if not project_number_or_id:
        return ""
    return f"https://yesabregistry.ca/api/v1/integration/projects/{project_number_or_id}"


def name_from_item(item: Any) -> str:
    """Return a human-readable name from a registry list item."""
    if isinstance(item, dict):
        for key in ("name", "displayName", "title"):
            name = text_value(item.get(key)).strip()
            if name:
                return name
        return text_value(item).strip()
    return text_value(item).strip()


def id_from_item(item: Any, *keys: str) -> str:
    """Return an ID-like value from a registry list item."""
    if not isinstance(item, dict):
        return ""
    for key in keys:
        value = text_value(item.get(key)).strip()
        if value:
            return value
    return ""


def names_from_items(items: Any) -> str:
    """Flatten a list of names into display text."""
    if not isinstance(items, list):
        return ""
    return ", ".join(name for item in items if (name := name_from_item(item)))


def compact_json(value: Any) -> str:
    """Return JSON for storing raw source fragments."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def load_projects_from_cache(api_cache_path: Path) -> list[dict[str, Any]]:
    """Load projects from the compressed merged API cache."""
    import compression.zstd as zstd

    api_cache_path = repo_path(api_cache_path)
    data = json.loads(zstd.decompress(api_cache_path.read_bytes()).decode("utf-8"))
    if isinstance(data, dict):
        projects = data.get("projects", [])
    else:
        projects = data
    if not isinstance(projects, list):
        raise ValueError(f"API cache did not contain a project list: {api_cache_path}")
    return sorted(
        (project for project in projects if isinstance(project, dict)),
        key=lambda project: text_value(project.get("projectNumber")),
    )


def load_default_map_payload() -> dict[str, Any]:
    """Load the same joined map payload used by the static map builders."""
    from scripts.build_static_map_single import load_layers

    payload = load_layers()
    if not isinstance(payload, dict):
        raise ValueError("Static map payload loader returned a non-dict value")
    return payload


def projects_from_map_payload(map_payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract API project rows from the static map payload."""
    api_projects = map_payload.get("apiProjects", {})
    if not isinstance(api_projects, dict):
        return []
    return sorted(
        (project for project in api_projects.values() if isinstance(project, dict)),
        key=lambda project: text_value(project.get("projectNumber")),
    )


def first_location(project: dict[str, Any]) -> dict[str, Any]:
    """Return the first location dict for a project, if available."""
    locations = project.get("locations")
    if isinstance(locations, list):
        for location in locations:
            if isinstance(location, dict):
                return location
    return {}


def flattened_project(project: dict[str, Any]) -> dict[str, Any]:
    """Return the projects table row for an API project."""
    stage = project.get("stage") if isinstance(project.get("stage"), dict) else {}
    outcomes = (
        project.get("outcomes") if isinstance(project.get("outcomes"), dict) else {}
    )
    scope = (
        project.get("projectScope")
        if isinstance(project.get("projectScope"), dict)
        else {}
    )
    cache = project.get("_cache") if isinstance(project.get("_cache"), dict) else {}
    location = first_location(project)
    locations = project.get("locations") if isinstance(project.get("locations"), list) else []
    project_number = text_value(project.get("projectNumber"))
    project_id = text_value(project.get("projectId"))
    return {
        "project_number": project_number,
        "project_year": project_year(project_number),
        "project_id": project_id,
        "title": text_value(project.get("title")),
        "project_type": text_value(project.get("projectTypeName")),
        "project_type_id": text_value(project.get("projectTypeId")),
        "proponent": text_value(project.get("proponentName")),
        "stage_name": text_value(stage.get("name")),
        "stage_id": text_value(stage.get("stageId") or project.get("stageId")),
        "stage_extended": bool_int(stage.get("extended")),
        "days_remaining": int_value(stage.get("daysRemaining")),
        "outcome_name": text_value(outcomes.get("outcomeName")),
        "decision_name": text_value(outcomes.get("decisionName")),
        "scope_summary": text_value(scope.get("summary")),
        "scope_activities": text_value(scope.get("activities")),
        "districts": names_from_items(project.get("assessmentDistricts")),
        "sectors": names_from_items(project.get("sectors")),
        "indigenous_governments": names_from_items(
            project.get("indigenousGovernments")
        ),
        "decision_bodies": names_from_items(project.get("decisionBodies")),
        "planning_commissions": names_from_items(project.get("planningCommissions")),
        "location_count": len(locations),
        "first_latitude": float_value(location.get("latitude")),
        "first_longitude": float_value(location.get("longitude")),
        "registry_page_url": registry_page_url(project_id),
        "registry_api_url": registry_api_url(project_number or project_id),
        "cache_bucket": text_value(cache.get("bucket")),
        "cache_cached_at": text_value(cache.get("bucketCachedAt")),
        "raw_json": compact_json(project),
    }


def project_number_from_feature(feature: dict[str, Any]) -> str:
    """Return the joined or source project number for a map feature."""
    api_project_number = text_value(feature.get("apiProjectNumber")).strip()
    if api_project_number:
        return api_project_number
    properties = feature.get("properties")
    if isinstance(properties, dict):
        for field in PROJECT_NUMBER_FIELDS:
            value = text_value(properties.get(field)).strip()
            if value:
                return value
    return ""


def feature_bbox(feature: dict[str, Any]) -> tuple[float | None, float | None, float | None, float | None]:
    """Return feature bounding box values."""
    bbox = feature.get("bbox")
    if not isinstance(bbox, list) or len(bbox) != 4:
        return (None, None, None, None)
    return (
        float_value(bbox[0]),
        float_value(bbox[1]),
        float_value(bbox[2]),
        float_value(bbox[3]),
    )


def iter_map_feature_rows(map_payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Flatten static map features into rows for lightweight join inspection."""
    if not map_payload:
        return []
    rows: list[dict[str, Any]] = []
    for layer in map_payload.get("layers", []):
        if not isinstance(layer, dict):
            continue
        layer_name = text_value(layer.get("name"))
        layer_type = text_value(layer.get("type"))
        source_archive = text_value(layer.get("archive"))
        for feature in layer.get("features", []):
            if not isinstance(feature, dict):
                continue
            geometry = feature.get("geometry") if isinstance(feature.get("geometry"), dict) else {}
            geometry_type = text_value(geometry.get("type") or layer_type)
            project_number = project_number_from_feature(feature)
            api_project_number = text_value(feature.get("apiProjectNumber")).strip()
            properties = feature.get("properties")
            if not isinstance(properties, dict):
                properties = {}
            min_x, min_y, max_x, max_y = feature_bbox(feature)
            rows.append(
                {
                    "project_number": project_number,
                    "layer_name": layer_name,
                    "source_archive": source_archive,
                    "source_feature_id": text_value(feature.get("id")),
                    "feature_label": text_value(feature.get("label")),
                    "geometry_type": geometry_type,
                    "geometry_source": text_value(
                        properties.get("locationSource") or source_archive
                    ),
                    "api_join_status": "joined" if api_project_number else "unmatched",
                    "bbox_min_x": min_x,
                    "bbox_min_y": min_y,
                    "bbox_max_x": max_x,
                    "bbox_max_y": max_y,
                    "properties_json": compact_json(properties),
                }
            )
    return rows


def split_bundle_path(path_text: str) -> list[str]:
    """Split a manifest path that may contain Windows or POSIX separators."""
    return [part for part in re.split(r"[\\/]+", path_text) if part]


def iter_bundle_manifests(bundle_root: Path | None) -> list[tuple[Path, dict[str, Any]]]:
    """Load project bundle manifests below ``bundle_root``."""
    if bundle_root is None:
        return []
    bundle_root = repo_path(bundle_root)
    if not bundle_root.exists():
        return []
    manifests: list[tuple[Path, dict[str, Any]]] = []
    for manifest_path in sorted(bundle_root.glob("*/manifest.json")):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(manifest, dict):
            manifests.append((manifest_path, manifest))
    return manifests


def create_schema(db: sqlite3.Connection) -> None:
    """Create the explorer schema."""
    db.executescript(
        """
        PRAGMA foreign_keys = ON;

        CREATE TABLE projects (
          id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
          project_number TEXT UNIQUE NOT NULL,
          project_year INTEGER,
          project_id TEXT,
          title TEXT,
          project_type TEXT,
          project_type_id TEXT,
          proponent TEXT,
          stage_name TEXT,
          stage_id TEXT,
          stage_extended INTEGER NOT NULL DEFAULT 0,
          days_remaining INTEGER,
          outcome_name TEXT,
          decision_name TEXT,
          scope_summary TEXT,
          scope_activities TEXT,
          districts TEXT,
          sectors TEXT,
          indigenous_governments TEXT,
          decision_bodies TEXT,
          planning_commissions TEXT,
          location_count INTEGER NOT NULL DEFAULT 0,
          first_latitude REAL,
          first_longitude REAL,
          registry_page_url TEXT,
          registry_api_url TEXT,
          cache_bucket TEXT,
          cache_cached_at TEXT,
          raw_json TEXT
        );

        CREATE TABLE project_locations (
          id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
          project_number TEXT NOT NULL REFERENCES projects(project_number),
          location_index INTEGER NOT NULL,
          latitude REAL,
          longitude REAL,
          raw_json TEXT
        );

        CREATE TABLE project_sectors (
          id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
          project_number TEXT NOT NULL REFERENCES projects(project_number),
          sector_id TEXT,
          sector_name TEXT
        );

        CREATE TABLE project_districts (
          id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
          project_number TEXT NOT NULL REFERENCES projects(project_number),
          district_id TEXT,
          district_name TEXT
        );

        CREATE TABLE project_indigenous_governments (
          id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
          project_number TEXT NOT NULL REFERENCES projects(project_number),
          name TEXT
        );

        CREATE TABLE project_decision_bodies (
          id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
          project_number TEXT NOT NULL REFERENCES projects(project_number),
          decision_body_id TEXT,
          decision_body_name TEXT
        );

        CREATE TABLE project_planning_commissions (
          id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
          project_number TEXT NOT NULL REFERENCES projects(project_number),
          planning_commission_id TEXT,
          planning_commission_name TEXT
        );

        CREATE TABLE project_stage_history (
          id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
          project_number TEXT NOT NULL REFERENCES projects(project_number),
          history_index INTEGER NOT NULL,
          stage_name TEXT,
          event_date TEXT,
          raw_json TEXT
        );

        CREATE TABLE map_features (
          id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
          project_number TEXT,
          layer_name TEXT,
          source_archive TEXT,
          source_feature_id TEXT,
          feature_label TEXT,
          geometry_type TEXT,
          geometry_source TEXT,
          api_join_status TEXT,
          bbox_min_x REAL,
          bbox_min_y REAL,
          bbox_max_x REAL,
          bbox_max_y REAL,
          properties_json TEXT
        );

        CREATE TABLE project_bundles (
          id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
          project_number TEXT,
          project_id TEXT,
          project_ref TEXT,
          title TEXT,
          generated_at TEXT,
          source_base_url TEXT,
          section_count INTEGER,
          attachment_count INTEGER,
          downloaded_attachment_count INTEGER,
          error_count INTEGER,
          bundle_dir TEXT,
          manifest_path TEXT
        );

        CREATE TABLE bundle_sections (
          id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
          project_number TEXT,
          name TEXT,
          endpoint TEXT,
          local_path TEXT,
          row_count INTEGER
        );

        CREATE TABLE bundle_attachments (
          id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
          project_number TEXT,
          document_number TEXT,
          document_type TEXT,
          source_kind TEXT,
          description TEXT,
          file_name TEXT,
          local_path TEXT,
          bytes INTEGER,
          content_type TEXT,
          downloaded INTEGER NOT NULL DEFAULT 0,
          timestamp_iso TEXT,
          upload_id TEXT,
          document_id TEXT
        );

        CREATE TABLE explorer_summary (
          id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
          item TEXT,
          value TEXT
        );

        CREATE INDEX idx_projects_number ON projects(project_number);
        CREATE INDEX idx_projects_year ON projects(project_year);
        CREATE INDEX idx_projects_stage ON projects(stage_name);
        CREATE INDEX idx_projects_type ON projects(project_type);
        CREATE INDEX idx_project_sectors_project ON project_sectors(project_number);
        CREATE INDEX idx_project_sectors_name ON project_sectors(sector_name);
        CREATE INDEX idx_project_districts_project ON project_districts(project_number);
        CREATE INDEX idx_project_districts_name ON project_districts(district_name);
        CREATE INDEX idx_map_features_project ON map_features(project_number);
        CREATE INDEX idx_map_features_layer ON map_features(layer_name);
        CREATE INDEX idx_bundle_attachments_project ON bundle_attachments(project_number);

        CREATE VIEW projects_with_bundles AS
        SELECT p.project_number,
               p.title,
               p.project_type,
               p.proponent,
               p.stage_name,
               b.generated_at,
               b.attachment_count,
               b.downloaded_attachment_count,
               b.bundle_dir
          FROM projects p
          JOIN project_bundles b USING (project_number);

        CREATE VIEW projects_by_year_type AS
        SELECT project_year,
               project_type,
               count(*) AS project_count
          FROM projects
         GROUP BY project_year, project_type;

        CREATE VIEW projects_by_district_sector AS
        SELECT d.district_name,
               s.sector_name,
               count(DISTINCT p.project_number) AS project_count
          FROM projects p
          LEFT JOIN project_districts d USING (project_number)
          LEFT JOIN project_sectors s USING (project_number)
         GROUP BY d.district_name, s.sector_name;

        CREATE VIEW map_join_summary AS
        SELECT layer_name,
               geometry_type,
               api_join_status,
               count(*) AS feature_count
          FROM map_features
         GROUP BY layer_name, geometry_type, api_join_status;

        CREATE VIEW unmapped_projects AS
        SELECT p.project_number,
               p.project_year,
               p.title,
               p.project_type,
               p.proponent,
               p.stage_name,
               p.districts,
               p.sectors
          FROM projects p
          LEFT JOIN map_features f ON f.project_number = p.project_number
         WHERE f.project_number IS NULL;
        """
    )


def insert_projects(db: sqlite3.Connection, projects: list[dict[str, Any]]) -> int:
    """Insert API projects and normalized relationship rows."""
    project_columns = list(flattened_project({}).keys())
    placeholders = ", ".join("?" for _ in project_columns)
    column_sql = ", ".join(project_columns)
    insert_sql = (
        f"INSERT INTO projects ({column_sql}) VALUES ({placeholders})"
    )
    count = 0
    for project in projects:
        row = flattened_project(project)
        project_number = row["project_number"]
        if not project_number:
            continue
        db.execute(insert_sql, [row[column] for column in project_columns])
        count += 1
        insert_project_relationships(db, project, project_number)
    return count


def insert_project_relationships(
    db: sqlite3.Connection, project: dict[str, Any], project_number: str
) -> None:
    """Insert project child tables."""
    locations = project.get("locations")
    if isinstance(locations, list):
        for index, location in enumerate(locations):
            if isinstance(location, dict):
                db.execute(
                    """
                    INSERT INTO project_locations
                      (project_number, location_index, latitude, longitude, raw_json)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        project_number,
                        index,
                        float_value(location.get("latitude")),
                        float_value(location.get("longitude")),
                        compact_json(location),
                    ),
                )

    insert_named_items(
        db,
        "project_sectors",
        project_number,
        project.get("sectors"),
        id_column="sector_id",
        name_column="sector_name",
        id_keys=("sectorId", "id"),
    )
    insert_named_items(
        db,
        "project_districts",
        project_number,
        project.get("assessmentDistricts"),
        id_column="district_id",
        name_column="district_name",
        id_keys=("assessmentDistrictId", "districtId", "id"),
    )
    insert_name_only_items(
        db,
        "project_indigenous_governments",
        project_number,
        project.get("indigenousGovernments"),
    )
    insert_named_items(
        db,
        "project_decision_bodies",
        project_number,
        project.get("decisionBodies"),
        id_column="decision_body_id",
        name_column="decision_body_name",
        id_keys=("decisionBodyId", "id"),
    )
    insert_named_items(
        db,
        "project_planning_commissions",
        project_number,
        project.get("planningCommissions"),
        id_column="planning_commission_id",
        name_column="planning_commission_name",
        id_keys=("planningCommissionId", "id"),
    )

    stage_history = project.get("stageHistory")
    if isinstance(stage_history, list):
        for index, item in enumerate(stage_history):
            if not isinstance(item, dict):
                continue
            db.execute(
                """
                INSERT INTO project_stage_history
                  (project_number, history_index, stage_name, event_date, raw_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    project_number,
                    index,
                    text_value(
                        item.get("stageName")
                        or item.get("name")
                        or item.get("stage")
                    ),
                    text_value(
                        item.get("date")
                        or item.get("startDate")
                        or item.get("createdDate")
                    ),
                    compact_json(item),
                ),
            )


def insert_named_items(
    db: sqlite3.Connection,
    table: str,
    project_number: str,
    items: Any,
    *,
    id_column: str,
    name_column: str,
    id_keys: tuple[str, ...],
) -> None:
    """Insert list items with an optional ID and name."""
    if not isinstance(items, list):
        return
    sql = (
        f"INSERT INTO {table} (project_number, {id_column}, {name_column}) "
        "VALUES (?, ?, ?)"
    )
    for item in items:
        name = name_from_item(item)
        if not name:
            continue
        db.execute(sql, (project_number, id_from_item(item, *id_keys), name))


def insert_name_only_items(
    db: sqlite3.Connection, table: str, project_number: str, items: Any
) -> None:
    """Insert list items that only need a name column."""
    if not isinstance(items, list):
        return
    for item in items:
        name = name_from_item(item)
        if name:
            db.execute(
                f"INSERT INTO {table} (project_number, name) VALUES (?, ?)",
                (project_number, name),
            )


def insert_map_features(
    db: sqlite3.Connection, map_payload: dict[str, Any] | None
) -> int:
    """Insert flattened map feature rows."""
    rows = iter_map_feature_rows(map_payload)
    db.executemany(
        """
        INSERT INTO map_features
          (project_number, layer_name, source_archive, source_feature_id,
           feature_label, geometry_type, geometry_source, api_join_status,
           bbox_min_x, bbox_min_y, bbox_max_x, bbox_max_y, properties_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                row["project_number"],
                row["layer_name"],
                row["source_archive"],
                row["source_feature_id"],
                row["feature_label"],
                row["geometry_type"],
                row["geometry_source"],
                row["api_join_status"],
                row["bbox_min_x"],
                row["bbox_min_y"],
                row["bbox_max_x"],
                row["bbox_max_y"],
                row["properties_json"],
            )
            for row in rows
        ],
    )
    return len(rows)


def insert_bundles(db: sqlite3.Connection, bundle_root: Path | None) -> dict[str, int]:
    """Insert locally downloaded project bundle manifest summaries."""
    bundle_count = 0
    section_count = 0
    attachment_count = 0
    for manifest_path, manifest in iter_bundle_manifests(bundle_root):
        project_number = text_value(manifest.get("projectNumber"))
        bundle_dir = manifest_path.parent
        db.execute(
            """
            INSERT INTO project_bundles
              (project_number, project_id, project_ref, title, generated_at,
               source_base_url, section_count, attachment_count,
               downloaded_attachment_count, error_count, bundle_dir, manifest_path)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_number,
                text_value(manifest.get("projectId")),
                text_value(manifest.get("projectRef")),
                text_value(manifest.get("title")),
                text_value(manifest.get("generatedAt")),
                text_value(manifest.get("sourceBaseUrl")),
                int_value(manifest.get("sectionCount")),
                int_value(manifest.get("attachmentCount")),
                int_value(manifest.get("downloadedAttachmentCount")),
                len(manifest.get("errors", []))
                if isinstance(manifest.get("errors"), list)
                else 0,
                str(bundle_dir),
                str(manifest_path),
            ),
        )
        bundle_count += 1

        sections = manifest.get("sections")
        if isinstance(sections, list):
            for section in sections:
                if not isinstance(section, dict):
                    continue
                section_path = text_value(section.get("path"))
                local_path = ""
                if section_path:
                    local_path = str(bundle_dir.joinpath(*split_bundle_path(section_path)))
                db.execute(
                    """
                    INSERT INTO bundle_sections
                      (project_number, name, endpoint, local_path, row_count)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        project_number,
                        text_value(section.get("name")),
                        text_value(section.get("endpoint")),
                        local_path,
                        int_value(section.get("count")),
                    ),
                )
                section_count += 1

        attachments = manifest.get("attachments")
        if isinstance(attachments, list):
            for attachment in attachments:
                if not isinstance(attachment, dict):
                    continue
                attachment_path = text_value(attachment.get("path"))
                local_path = ""
                if attachment_path:
                    local_path = str(
                        bundle_dir.joinpath(*split_bundle_path(attachment_path))
                    )
                db.execute(
                    """
                    INSERT INTO bundle_attachments
                      (project_number, document_number, document_type, source_kind,
                       description, file_name, local_path, bytes, content_type,
                       downloaded, timestamp_iso, upload_id, document_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project_number,
                        text_value(attachment.get("documentNumber")),
                        text_value(attachment.get("documentType")),
                        text_value(attachment.get("sourceKind")),
                        text_value(attachment.get("description")),
                        text_value(
                            attachment.get("fileName")
                            or attachment.get("originalFilename")
                            or attachment.get("redactedFileName")
                        ),
                        local_path,
                        int_value(attachment.get("bytes")),
                        text_value(attachment.get("contentType")),
                        bool_int(attachment.get("downloaded")),
                        text_value(attachment.get("timestampIso")),
                        text_value(attachment.get("uploadId")),
                        text_value(attachment.get("documentId")),
                    ),
                )
                attachment_count += 1

    return {
        "project_bundles": bundle_count,
        "bundle_sections": section_count,
        "bundle_attachments": attachment_count,
    }


def create_fts(db: sqlite3.Connection) -> None:
    """Create and populate the projects full-text search table."""
    db.executescript(
        """
        CREATE VIRTUAL TABLE projects_fts USING fts5(
          project_number,
          title,
          proponent,
          project_type,
          stage_name,
          districts,
          sectors,
          scope_summary,
          content='projects',
          content_rowid='id'
        );
        INSERT INTO projects_fts(projects_fts) VALUES ('rebuild');
        """
    )


def write_summary(
    db: sqlite3.Connection,
    counts: dict[str, int],
    map_payload: dict[str, Any] | None,
    built_at: str,
) -> None:
    """Write a small machine-readable export summary table."""
    rows = [
        ("built_at_utc", built_at),
        ("counts", compact_json(counts)),
    ]
    if map_payload:
        rows.extend(
            [
                ("api_summary", compact_json(map_payload.get("apiSummary", {}))),
                ("qa_summary", compact_json(map_payload.get("qa", {}).get("summary", {}))),
            ]
        )
    db.executemany(
        "INSERT INTO explorer_summary (item, value) VALUES (?, ?)",
        rows,
    )


def write_explorer_db(
    output_path: Path,
    projects: list[dict[str, Any]],
    *,
    map_payload: dict[str, Any] | None = None,
    bundle_root: Path | None = DEFAULT_BUNDLE_ROOT,
) -> dict[str, int]:
    """Write the Datasette explorer SQLite database and return row counts."""
    output_path = repo_path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()

    built_at = now_utc()
    with closing(sqlite3.connect(output_path)) as db:
        create_schema(db)
        counts: dict[str, int] = {
            "projects": insert_projects(db, projects),
            "map_features": insert_map_features(db, map_payload),
        }
        counts.update(insert_bundles(db, bundle_root))
        create_fts(db)
        counts["projects_fts"] = counts["projects"]
        write_summary(db, counts, map_payload, built_at)
        db.commit()
        db.execute("VACUUM")
    return counts


def datasette_metadata(database_name: str) -> dict[str, Any]:
    """Return Datasette metadata/config for the generated database."""
    return {
        "title": "YESAB Offline Explorer",
        "description": (
            "Queryable local YESAB project cache built from the registry API, "
            "project map joins, and downloaded project bundles."
        ),
        "license": "Open Government Licence - Yukon",
        "license_url": "https://open.yukon.ca/open-government-licence-yukon",
        "source": "YESAB Registry and YESAB Project Map",
        "source_url": "https://yesabregistry.ca/",
        "databases": {
            database_name: {
                "tables": {
                    "projects": {
                        "label_column": "project_number",
                        "facets": [
                            "project_year",
                            "project_type",
                            "stage_name",
                            "districts",
                            "sectors",
                        ],
                        "plugins": {
                            "datasette-cluster-map": {
                                "latitude_column": "first_latitude",
                                "longitude_column": "first_longitude",
                            }
                        },
                    },
                    "project_sectors": {"facets": ["sector_name"]},
                    "project_districts": {"facets": ["district_name"]},
                    "map_features": {
                        "facets": [
                            "layer_name",
                            "geometry_type",
                            "api_join_status",
                        ]
                    },
                    "bundle_attachments": {
                        "facets": [
                            "project_number",
                            "document_type",
                            "source_kind",
                            "content_type",
                            "downloaded",
                        ]
                    },
                },
                "queries": {
                    "active_projects": {
                        "title": "Projects not yet at a final decision stage",
                        "sql": """
                            SELECT project_number, title, project_type, proponent,
                                   stage_name, days_remaining, districts, sectors
                              FROM projects
                             WHERE stage_name IS NOT NULL
                               AND lower(stage_name) NOT LIKE '%decision document issued%'
                             ORDER BY project_year DESC, project_number DESC
                        """,
                    },
                    "projects_by_sector": {
                        "title": "Project counts by sector",
                        "sql": """
                            SELECT sector_name, count(DISTINCT project_number) AS project_count
                              FROM project_sectors
                             GROUP BY sector_name
                             ORDER BY project_count DESC, sector_name
                        """,
                    },
                    "projects_by_district": {
                        "title": "Project counts by assessment district",
                        "sql": """
                            SELECT district_name, count(DISTINCT project_number) AS project_count
                              FROM project_districts
                             GROUP BY district_name
                             ORDER BY project_count DESC, district_name
                        """,
                    },
                    "mapped_project_features": {
                        "title": "Map feature join summary",
                        "sql": """
                            SELECT layer_name, geometry_type, api_join_status,
                                   count(*) AS feature_count
                              FROM map_features
                             GROUP BY layer_name, geometry_type, api_join_status
                             ORDER BY layer_name, api_join_status
                        """,
                    },
                    "unmapped_projects": {
                        "title": "Projects with no map feature row",
                        "sql": """
                            SELECT project_number, title, project_type, proponent,
                                   stage_name, districts, sectors
                              FROM unmapped_projects
                             ORDER BY project_year DESC, project_number DESC
                        """,
                    },
                    "downloaded_bundle_documents": {
                        "title": "Downloaded project bundle attachments",
                        "sql": """
                            SELECT project_number, document_number, document_type,
                                   source_kind, description, file_name, local_path,
                                   bytes, timestamp_iso
                              FROM bundle_attachments
                             WHERE downloaded = 1
                             ORDER BY project_number DESC, document_number
                        """,
                    },
                },
            }
        },
    }


def write_datasette_metadata(metadata_path: Path, *, database_name: str) -> None:
    """Write Datasette metadata JSON for the generated explorer database."""
    metadata_path = repo_path(metadata_path)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(
        json.dumps(
            datasette_metadata(database_name),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )


def default_metadata_path(output_path: Path) -> Path:
    """Return the default metadata JSON path for an explorer database."""
    return output_path.with_name(f"{output_path.stem}.metadata.json")


def build_explorer(
    output_path: Path = DEFAULT_OUTPUT_PATH,
    *,
    metadata_output: Path | None = None,
    api_cache_path: Path = DEFAULT_API_CACHE_PATH,
    bundle_root: Path | None = DEFAULT_BUNDLE_ROOT,
    include_map_features: bool = True,
) -> dict[str, int]:
    """Build the explorer database and companion Datasette metadata file."""
    output_path = repo_path(output_path)
    metadata_output = repo_path(metadata_output or default_metadata_path(output_path))

    map_payload: dict[str, Any] | None = None
    if include_map_features:
        map_payload = load_default_map_payload()
        projects = projects_from_map_payload(map_payload)
    else:
        projects = load_projects_from_cache(api_cache_path)

    if not projects:
        projects = load_projects_from_cache(api_cache_path)

    counts = write_explorer_db(
        output_path,
        projects,
        map_payload=map_payload,
        bundle_root=bundle_root,
    )
    write_datasette_metadata(metadata_output, database_name=output_path.stem)
    counts["metadata_files"] = 1
    return counts


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "output",
        nargs="?",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help=f"SQLite explorer output path (default: {DEFAULT_OUTPUT_PATH})",
    )
    parser.add_argument(
        "--metadata-output",
        type=Path,
        help="Datasette metadata JSON path (default: next to output DB)",
    )
    parser.add_argument(
        "--api-cache",
        type=Path,
        default=DEFAULT_API_CACHE_PATH,
        help=(
            "Merged API cache to use when --no-map-features is supplied "
            f"(default: {DEFAULT_API_CACHE_PATH})"
        ),
    )
    parser.add_argument(
        "--bundle-root",
        type=Path,
        default=DEFAULT_BUNDLE_ROOT,
        help=f"Project bundle root to import (default: {DEFAULT_BUNDLE_ROOT})",
    )
    parser.add_argument(
        "--no-bundles",
        action="store_true",
        help="Do not import local out/project-bundles manifests.",
    )
    parser.add_argument(
        "--no-map-features",
        action="store_true",
        help=(
            "Skip shapefile/map payload loading and build only from the API cache. "
            "This is faster but omits map_features and map join QA tables."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Build the Datasette explorer outputs."""
    args = parse_args(argv)
    output_path = repo_path(args.output)
    metadata_path = repo_path(args.metadata_output or default_metadata_path(output_path))
    counts = build_explorer(
        output_path,
        metadata_output=metadata_path,
        api_cache_path=args.api_cache,
        bundle_root=None if args.no_bundles else args.bundle_root,
        include_map_features=not args.no_map_features,
    )
    print(f"Wrote {output_path}")
    print(f"Wrote {metadata_path}")
    for item, count in counts.items():
        print(f"  {item}: {count}")
    print()
    print("Run with Datasette, for example:")
    print(
        "  uvx --with datasette-cluster-map datasette "
        f"{output_path} -m {metadata_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
