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
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


DEFAULT_OUTPUT_PATH = Path("./out/yesab-explorer.db")
DEFAULT_API_CACHE_PATH = Path("./data/api/projects_merged.json.zst")
DEFAULT_BUNDLE_ROOT = Path("./out/project-bundles")
DEFAULT_BUNDLE_STATIC_MOUNT = "/bundles/"

PROJECT_NUMBER_FIELDS = ("ProjectID", "Prj_ID", "YESAB_PROJ", "Number")
YUKON_BASEMAP_TILE_LAYER = "/-/yesab-yukon-basemap/topo/{z}/{x}/{y}.png"
YUKON_BASEMAP_ATTRIBUTION = (
    'Basemap: &copy; <a href="https://yukon.ca/">Government of Yukon</a>'
)
YUKON_BASEMAP_TILE_OPTIONS = {
    "attribution": YUKON_BASEMAP_ATTRIBUTION,
    "maxZoom": 19,
}


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


def normalized_bundle_path(path_text: str) -> str:
    """Return a stable POSIX-style manifest path string."""
    return "/".join(split_bundle_path(path_text))


def normalized_static_mount(static_mount: str) -> str:
    """Return a root-relative Datasette static mount path with trailing slash."""
    mount = static_mount.strip().replace("\\", "/")
    mount = mount.strip("/")
    return f"/{mount}/" if mount else "/"


def quoted_posix_path(parts: tuple[str, ...]) -> str:
    """Return URL-encoded POSIX path text from already validated path parts."""
    return "/".join(quote(part, safe="") for part in parts)


def safe_bundle_path(bundle_dir: Path, path_text: str) -> Path | None:
    """Return a manifest path only when it stays below ``bundle_dir``."""
    parts = split_bundle_path(path_text)
    if not parts or any(part in {".", ".."} for part in parts):
        return None
    try:
        bundle_root = bundle_dir.resolve()
        candidate = bundle_dir.joinpath(*parts).resolve()
    except OSError:
        return None
    if candidate == bundle_root or bundle_root in candidate.parents:
        return candidate
    return None


def empty_bundle_attachment_link_fields() -> dict[str, str]:
    """Return blank attachment link fields for unsafe or missing paths."""
    return {"local_path": "", "bundle_path": "", "datasette_url": ""}


def bundle_attachment_link_fields(
    bundle_root: Path | None,
    bundle_dir: Path,
    path_text: str,
    *,
    static_mount: str = DEFAULT_BUNDLE_STATIC_MOUNT,
) -> dict[str, str]:
    """Return safe filesystem and Datasette static-link fields for an attachment.

    ``bundle_path`` is URL-encoded relative to the configured bundle root. The
    ``datasette_url`` prefixes that path with the explicit Datasette static
    mount. Blank fields are returned if the manifest path would resolve outside
    either the project bundle directory or the configured bundle root.
    """
    if bundle_root is None or not path_text:
        return empty_bundle_attachment_link_fields()

    local_path = safe_bundle_path(bundle_dir, path_text)
    if local_path is None:
        return empty_bundle_attachment_link_fields()

    try:
        resolved_root = repo_path(bundle_root).resolve()
        resolved_local_path = local_path.resolve()
        relative_path = resolved_local_path.relative_to(resolved_root)
    except (OSError, ValueError):
        return empty_bundle_attachment_link_fields()

    if any(part in {"", ".", ".."} for part in relative_path.parts):
        return empty_bundle_attachment_link_fields()

    bundle_path = quoted_posix_path(relative_path.parts)
    return {
        "local_path": str(resolved_local_path),
        "bundle_path": bundle_path,
        "datasette_url": f"{normalized_static_mount(static_mount)}{bundle_path}",
    }


def read_json_or_none(path: Path) -> Any | None:
    """Read a JSON file, returning None when it is missing or invalid."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def bundle_section_entries(
    bundle_dir: Path, sections: Any
) -> list[dict[str, Any]]:
    """Return manifest section rows plus optional loaded JSON payloads."""
    if not isinstance(sections, list):
        return []
    entries: list[dict[str, Any]] = []
    for section in sections:
        if not isinstance(section, dict):
            continue
        path_text = text_value(section.get("path"))
        path = safe_bundle_path(bundle_dir, path_text) if path_text else None
        payload = read_json_or_none(path) if path is not None else None
        entries.append(
            {
                "section": section,
                "name": text_value(section.get("name")),
                "endpoint": text_value(section.get("endpoint")),
                "source_json_path": normalized_bundle_path(path_text),
                "source_local_path": str(path) if path is not None else "",
                "payload": payload,
            }
        )
    return entries


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


def date_iso_value(value: Any) -> str:
    """Return an ISO-like date string for registry date values."""
    if value is None or value == "":
        return ""
    if isinstance(value, int | float):
        if value <= 0:
            return ""
        timestamp = value / 1000 if value > 10_000_000_000 else value
        return (
            datetime.fromtimestamp(timestamp, UTC)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return ""
        if re.fullmatch(r"\d+(\.\d+)?", text):
            return date_iso_value(float(text))
        return text
    return text_value(value)


def first_text(item: dict[str, Any], *keys: str) -> str:
    """Return the first non-empty text value from ``item``."""
    for key in keys:
        value = text_value(item.get(key)).strip()
        if value:
            return value
    return ""


def unique_csv(values: list[str]) -> str:
    """Return comma-separated unique non-empty values, preserving order."""
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        value = value.strip()
        if value and value not in seen:
            seen.add(value)
            unique.append(value)
    return ", ".join(unique)


def upload_id_from_item(item: dict[str, Any]) -> str:
    """Return the preferred public upload ID from a document-like row."""
    return first_text(item, "redactedUploadId", "uploadId", "unredactedUploadId")


def document_parent_for_section(
    name: str,
) -> tuple[str, str, str] | None:
    """Return parent metadata for document-like bundle sections."""
    if name in {"documents", "key_documents", "correspondence_documents"}:
        return ("", "", "document")
    patterns = (
        (r"^comments/(.+)_documents$", "comment", "attachment"),
        (r"^notes_(.+)_documents$", "note", "attachment"),
        (r"^information[-_]requests_(.+)_documents$", "information_request", "attachment"),
        (
            r"^simplified[-_]information[-_]requests_(.+)_documents$",
            "simplified_information_request",
            "attachment",
        ),
        (r"^emails/(.+)$", "email", "attachment"),
    )
    for pattern, parent_kind, document_role in patterns:
        match = re.match(pattern, name)
        if match:
            return (parent_kind, match.group(1), document_role)
    return None


def iter_document_items(payload: Any, default_role: str) -> list[tuple[int, str, dict[str, Any]]]:
    """Return document-like rows from a section payload."""
    rows: list[tuple[int, str, dict[str, Any]]] = []
    if isinstance(payload, list):
        for index, item in enumerate(payload):
            if isinstance(item, dict):
                rows.append((index, default_role, item))
        return rows
    if not isinstance(payload, dict):
        return rows
    row_index = 0
    for role in ("documents", "questions", "responses", "replaced"):
        children = payload.get(role)
        if not isinstance(children, list):
            continue
        for item in children:
            if isinstance(item, dict):
                rows.append((row_index, role, item))
                row_index += 1
    return rows


def message_text(message: Any) -> str:
    """Return compact display text from an activity message payload."""
    if isinstance(message, list):
        parts: list[str] = []
        for part in message:
            if isinstance(part, dict):
                parts.append(text_value(part.get("message")))
            else:
                parts.append(text_value(part))
        return re.sub(r"\s+", " ", "".join(parts)).strip()
    return re.sub(r"\s+", " ", text_value(message)).strip()


def linked_document_ids(activity: dict[str, Any]) -> str:
    """Return linked document IDs from an activity feed item."""
    values: list[str] = []
    message = activity.get("message")
    if isinstance(message, list):
        for part in message:
            if isinstance(part, dict):
                values.append(text_value(part.get("linkTo")))
    documents = activity.get("documents")
    if isinstance(documents, list):
        for document in documents:
            if isinstance(document, dict):
                values.append(
                    first_text(document, "documentId", "id", "documentNumber")
                )
    return unique_csv(values)


def email_items(payload: Any) -> list[dict[str, Any]]:
    """Flatten top-level email/notification bundle payloads."""
    if isinstance(payload, dict):
        return [payload] if text_value(payload.get("emailMessageId")) else []
    if not isinstance(payload, list):
        return []
    rows: list[dict[str, Any]] = []
    for day in payload:
        if not isinstance(day, dict):
            continue
        email_date = text_value(day.get("emailDate"))
        for type_group in day.get("emailTypeGroups", []):
            if not isinstance(type_group, dict):
                continue
            for recipient_group in type_group.get("emailRecipientGroups", []):
                if not isinstance(recipient_group, dict):
                    continue
                for email in recipient_group.get("emails", []):
                    if not isinstance(email, dict):
                        continue
                    row = dict(email)
                    if email_date and not row.get("emailDate"):
                        row["emailDate"] = email_date
                    rows.append(row)
    return rows


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
          source_path TEXT,
          description TEXT,
          file_name TEXT,
          local_path TEXT,
          bundle_path TEXT,
          datasette_url TEXT,
          bytes INTEGER,
          content_type TEXT,
          downloaded INTEGER NOT NULL DEFAULT 0,
          timestamp_iso TEXT,
          upload_id TEXT,
          document_id TEXT
        );

        CREATE TABLE bundle_attachment_links (
          id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
          project_number TEXT,
          source_table TEXT,
          source_row_id INTEGER,
          attachment_id INTEGER,
          match_field TEXT,
          match_value TEXT,
          document_id TEXT,
          document_number TEXT,
          upload_id TEXT,
          datasette_url TEXT,
          local_path TEXT
        );

        CREATE TABLE bundle_attachment_link_qa (
          id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
          project_number TEXT,
          source_table TEXT,
          source_row_id INTEGER,
          attachment_id INTEGER,
          issue TEXT,
          match_field TEXT,
          match_value TEXT,
          candidate_attachment_ids TEXT,
          qa_note TEXT
        );

        CREATE TABLE bundle_documents (
          id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
          project_number TEXT,
          project_id TEXT,
          source_section TEXT,
          source_json_path TEXT,
          source_local_path TEXT,
          source_row_index INTEGER,
          parent_kind TEXT,
          parent_id TEXT,
          document_role TEXT,
          document_id TEXT,
          document_number TEXT,
          document_type TEXT,
          document_type_id TEXT,
          document_state TEXT,
          title TEXT,
          description TEXT,
          file_name TEXT,
          redacted_file_name TEXT,
          upload_id TEXT,
          upload_date TEXT,
          upload_date_iso TEXT,
          key_document INTEGER NOT NULL DEFAULT 0,
          is_historic INTEGER NOT NULL DEFAULT 0,
          stage_uploaded TEXT,
          raw_json TEXT
        );

        CREATE TABLE bundle_comments (
          id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
          project_number TEXT,
          project_id TEXT,
          source_section TEXT,
          source_json_path TEXT,
          source_local_path TEXT,
          source_row_index INTEGER,
          comment_id TEXT,
          document_number TEXT,
          submitter_name TEXT,
          first_name TEXT,
          last_name TEXT,
          redacted_comment TEXT,
          stage_uploaded TEXT,
          submitted_date TEXT,
          submitted_date_iso TEXT,
          document_count INTEGER NOT NULL DEFAULT 0,
          upload_ids TEXT,
          document_ids TEXT,
          raw_json TEXT
        );

        CREATE TABLE bundle_activity_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
          project_number TEXT,
          project_id TEXT,
          source_section TEXT,
          source_json_path TEXT,
          source_local_path TEXT,
          source_row_index INTEGER,
          activity_date TEXT,
          activity_date_iso TEXT,
          activity_date_formatted TEXT,
          message_text TEXT,
          linked_document_ids TEXT,
          document_count INTEGER NOT NULL DEFAULT 0,
          secondary_sort INTEGER,
          raw_json TEXT
        );

        CREATE TABLE bundle_notes (
          id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
          project_number TEXT,
          project_id TEXT,
          source_section TEXT,
          source_json_path TEXT,
          source_local_path TEXT,
          source_row_index INTEGER,
          note_id TEXT,
          document_number TEXT,
          title TEXT,
          note_html TEXT,
          note_state TEXT,
          stage_uploaded TEXT,
          published_date TEXT,
          published_date_iso TEXT,
          upload_date TEXT,
          upload_date_iso TEXT,
          uploaded_by TEXT,
          document_count INTEGER NOT NULL DEFAULT 0,
          upload_ids TEXT,
          document_ids TEXT,
          raw_json TEXT
        );

        CREATE TABLE bundle_simplified_information_requests (
          id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
          project_number TEXT,
          project_id TEXT,
          source_section TEXT,
          source_json_path TEXT,
          source_local_path TEXT,
          source_row_index INTEGER,
          request_id TEXT,
          request_number INTEGER,
          status TEXT,
          status_date TEXT,
          status_date_iso TEXT,
          published_date TEXT,
          published_date_iso TEXT,
          answered_date TEXT,
          answered_date_iso TEXT,
          project_stage TEXT,
          request_document_number TEXT,
          response_document_number TEXT,
          document_count INTEGER NOT NULL DEFAULT 0,
          upload_ids TEXT,
          document_ids TEXT,
          raw_json TEXT
        );

        CREATE TABLE bundle_emails (
          id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
          project_number TEXT,
          project_id TEXT,
          source_section TEXT,
          source_json_path TEXT,
          source_local_path TEXT,
          source_row_index INTEGER,
          email_message_id TEXT,
          email_date TEXT,
          sent_date TEXT,
          sent_date_iso TEXT,
          subject TEXT,
          message_type TEXT,
          message_type_id TEXT,
          recipient_type TEXT,
          recipient_type_id TEXT,
          stage_uploaded TEXT,
          content TEXT,
          document_count INTEGER NOT NULL DEFAULT 0,
          upload_ids TEXT,
          document_ids TEXT,
          raw_json TEXT
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
        CREATE INDEX idx_bundle_attachments_upload ON bundle_attachments(upload_id);
        CREATE INDEX idx_bundle_attachments_document_id ON bundle_attachments(document_id);
        CREATE INDEX idx_bundle_attachments_number ON bundle_attachments(document_number);
        CREATE INDEX idx_bundle_attachment_links_project ON bundle_attachment_links(project_number);
        CREATE INDEX idx_bundle_attachment_links_source ON bundle_attachment_links(source_table, source_row_id);
        CREATE INDEX idx_bundle_attachment_links_attachment ON bundle_attachment_links(attachment_id);
        CREATE INDEX idx_bundle_documents_project ON bundle_documents(project_number);
        CREATE INDEX idx_bundle_documents_number ON bundle_documents(document_number);
        CREATE INDEX idx_bundle_documents_upload ON bundle_documents(upload_id);
        CREATE INDEX idx_bundle_comments_project ON bundle_comments(project_number);
        CREATE INDEX idx_bundle_activity_project ON bundle_activity_events(project_number);
        CREATE INDEX idx_bundle_notes_project ON bundle_notes(project_number);
        CREATE INDEX idx_bundle_sir_project ON bundle_simplified_information_requests(project_number);
        CREATE INDEX idx_bundle_emails_project ON bundle_emails(project_number);

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

        CREATE VIEW bundle_documents_with_attachments AS
        SELECT d.id AS document_row_id,
               d.project_number,
               d.document_id,
               d.document_number,
               d.document_type,
               d.title,
               d.source_section,
               l.attachment_id,
               l.match_field,
               l.datasette_url,
               l.local_path
          FROM bundle_documents d
          LEFT JOIN bundle_attachment_links l
            ON l.source_table = 'bundle_documents'
           AND l.source_row_id = d.id;

        CREATE VIEW bundle_comments_with_attachments AS
        SELECT c.id AS comment_row_id,
               c.project_number,
               c.comment_id,
               c.document_number,
               c.submitter_name,
               c.document_count,
               l.attachment_id,
               l.match_field,
               l.datasette_url,
               l.local_path
          FROM bundle_comments c
          LEFT JOIN bundle_attachment_links l
            ON l.source_table = 'bundle_comments'
           AND l.source_row_id = c.id;

        CREATE VIEW bundle_activity_with_attachments AS
        SELECT a.id AS activity_row_id,
               a.project_number,
               a.activity_date_iso,
               a.message_text,
               a.linked_document_ids,
               l.attachment_id,
               l.match_field,
               l.datasette_url,
               l.local_path
          FROM bundle_activity_events a
          LEFT JOIN bundle_attachment_links l
            ON l.source_table = 'bundle_activity_events'
           AND l.source_row_id = a.id;
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


def add_parent_document_summary(
    summary: dict[tuple[str, str], dict[str, Any]],
    parent_kind: str,
    parent_id: str,
    *,
    document_id: str,
    upload_id: str,
) -> None:
    """Record document identifiers for a parent bundle entity."""
    if not parent_kind or not parent_id:
        return
    entry = summary.setdefault(
        (parent_kind, parent_id),
        {"count": 0, "document_ids": [], "upload_ids": []},
    )
    entry["count"] += 1
    if document_id:
        entry["document_ids"].append(document_id)
    if upload_id:
        entry["upload_ids"].append(upload_id)


def parent_document_values(
    summary: dict[tuple[str, str], dict[str, Any]],
    parent_kind: str,
    parent_id: str,
) -> tuple[int, str, str]:
    """Return document count, upload IDs, and document IDs for a parent."""
    entry = summary.get((parent_kind, parent_id), {})
    return (
        int_value(entry.get("count")) or 0,
        unique_csv(entry.get("upload_ids", [])),
        unique_csv(entry.get("document_ids", [])),
    )


def split_csv_values(value: str) -> list[str]:
    """Return non-empty values from a comma-separated identifier field."""
    return [part.strip() for part in value.split(",") if part.strip()]


def top_level_source_kind(value: str) -> str:
    """Return the attachment source kind implied by a normalized source section."""
    return value.split("/", 1)[0].strip()


def bundle_source_path(source_section: str, row_index: int | None) -> str:
    """Return the manifest sourcePath form for a normalized bundle row."""
    if not source_section or row_index is None:
        return ""
    return f"$.{source_section}[{row_index}]"


def attachment_link_sources(db: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return normalized rows that can reliably point to local attachments."""
    sources: list[dict[str, Any]] = []
    for row in db.execute(
        """
        SELECT id, project_number, document_id, document_number, upload_id,
               source_section, source_row_index
          FROM bundle_documents
        """
    ):
        sources.append(
            {
                "source_table": "bundle_documents",
                "source_row_id": row[0],
                "project_number": row[1],
                "document_id": row[2],
                "document_number": row[3],
                "upload_ids": split_csv_values(row[4] or ""),
                "document_ids": split_csv_values(row[2] or ""),
                "document_numbers": split_csv_values(row[3] or ""),
                "source_kind": top_level_source_kind(row[5] or ""),
                "source_path": bundle_source_path(row[5] or "", row[6]),
            }
        )
    for table in (
        "bundle_comments",
        "bundle_activity_events",
        "bundle_notes",
        "bundle_simplified_information_requests",
        "bundle_emails",
    ):
        if table == "bundle_activity_events":
            rows = db.execute(
                """
                SELECT id, project_number, linked_document_ids, '' AS upload_ids,
                       '' AS document_number, source_section
                  FROM bundle_activity_events
                """
            )
        else:
            rows = db.execute(
                f"""
                SELECT id, project_number, document_ids, upload_ids,
                       document_number, source_section
                  FROM {table}
                """
                if table in {"bundle_comments", "bundle_notes"}
                else f"""
                SELECT id, project_number, document_ids, upload_ids,
                       '' AS document_number, source_section
                  FROM {table}
                """
            )
        for row in rows:
            sources.append(
                {
                    "source_table": table,
                    "source_row_id": row[0],
                    "project_number": row[1],
                    "document_id": "",
                    "document_number": row[4] or "",
                    "upload_ids": split_csv_values(row[3] or ""),
                    "document_ids": split_csv_values(row[2] or ""),
                    "document_numbers": split_csv_values(row[4] or ""),
                    "source_kind": top_level_source_kind(row[5] or ""),
                    "source_path": "",
                }
            )
    return sources


def attachment_matches(
    db: sqlite3.Connection,
    *,
    project_number: str,
    field: str,
    value: str,
    source_kind: str = "",
) -> list[sqlite3.Row]:
    """Return candidate attachments for a source identifier."""
    if not value:
        return []
    if field == "upload_id":
        sql = """
            SELECT id, document_id, document_number, upload_id, datasette_url, local_path
              FROM bundle_attachments
             WHERE project_number = ? AND upload_id = ?
        """
        return list(db.execute(sql, (project_number, value)))
    if field == "document_id":
        sql = """
            SELECT id, document_id, document_number, upload_id, datasette_url, local_path
              FROM bundle_attachments
             WHERE project_number = ? AND document_id = ?
        """
        return list(db.execute(sql, (project_number, value)))
    if field == "document_number":
        sql = """
            SELECT id, document_id, document_number, upload_id, datasette_url, local_path
              FROM bundle_attachments
             WHERE project_number = ? AND document_number = ?
        """
        params: tuple[str, ...]
        params = (project_number, value)
        if source_kind:
            sql += " AND source_kind = ?"
            params = (project_number, value, source_kind)
        return list(db.execute(sql, params))
    if field == "source_path":
        sql = """
            SELECT id, document_id, document_number, upload_id, datasette_url, local_path
              FROM bundle_attachments
             WHERE project_number = ? AND source_path = ?
        """
        return list(db.execute(sql, (project_number, value)))
    return []


def insert_attachment_link(
    db: sqlite3.Connection,
    source: dict[str, Any],
    attachment: sqlite3.Row,
    *,
    match_field: str,
    match_value: str,
) -> bool:
    """Insert one source-to-attachment link unless it already exists."""
    exists = db.execute(
        """
        SELECT 1
          FROM bundle_attachment_links
         WHERE source_table = ? AND source_row_id = ? AND attachment_id = ?
        """,
        (source["source_table"], source["source_row_id"], attachment[0]),
    ).fetchone()
    if exists:
        return False
    db.execute(
        """
        INSERT INTO bundle_attachment_links
          (project_number, source_table, source_row_id, attachment_id, match_field,
           match_value, document_id, document_number, upload_id, datasette_url,
           local_path)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source["project_number"],
            source["source_table"],
            source["source_row_id"],
            attachment[0],
            match_field,
            match_value,
            attachment[1] or source.get("document_id", ""),
            attachment[2] or source.get("document_number", ""),
            (attachment[3] or match_value)
            if match_field == "upload_id"
            else attachment[3],
            attachment[4],
            attachment[5],
        ),
    )
    return True


def insert_attachment_link_qa(
    db: sqlite3.Connection,
    *,
    project_number: str,
    source_table: str = "",
    source_row_id: int | None = None,
    attachment_id: int | None = None,
    issue: str,
    match_field: str = "",
    match_value: str = "",
    candidate_attachment_ids: list[str] | None = None,
    qa_note: str = "",
) -> None:
    """Insert one attachment-link QA row."""
    db.execute(
        """
        INSERT INTO bundle_attachment_link_qa
          (project_number, source_table, source_row_id, attachment_id, issue,
           match_field, match_value, candidate_attachment_ids, qa_note)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            project_number,
            source_table,
            source_row_id,
            attachment_id,
            issue,
            match_field,
            match_value,
            unique_csv(candidate_attachment_ids or []),
            qa_note,
        ),
    )


def insert_bundle_attachment_links(db: sqlite3.Connection) -> dict[str, int]:
    """Link normalized bundle records to downloaded local attachments."""
    link_count = 0
    qa_count = 0
    for source in attachment_link_sources(db):
        matched = False
        ambiguous = False
        identifiers: list[tuple[str, str]] = []
        identifiers.extend(("upload_id", value) for value in source["upload_ids"])
        identifiers.extend(("document_id", value) for value in source["document_ids"])
        if source.get("source_path"):
            identifiers.append(("source_path", source["source_path"]))
        identifiers.extend(("document_number", value) for value in source["document_numbers"])
        for field, value in identifiers:
            candidates = attachment_matches(
                db,
                project_number=source["project_number"],
                field=field,
                value=value,
                source_kind=source.get("source_kind", ""),
            )
            if len(candidates) == 1:
                if insert_attachment_link(
                    db,
                    source,
                    candidates[0],
                    match_field=field,
                    match_value=value,
                ):
                    link_count += 1
                matched = True
                continue
            if len(candidates) > 1:
                insert_attachment_link_qa(
                    db,
                    project_number=source["project_number"],
                    source_table=source["source_table"],
                    source_row_id=source["source_row_id"],
                    issue="ambiguous_source_match",
                    match_field=field,
                    match_value=value,
                    candidate_attachment_ids=[str(candidate[0]) for candidate in candidates],
                    qa_note="Multiple attachments matched this source identifier.",
                )
                qa_count += 1
                ambiguous = True
                break
        if not matched and not ambiguous and identifiers:
            insert_attachment_link_qa(
                db,
                project_number=source["project_number"],
                source_table=source["source_table"],
                source_row_id=source["source_row_id"],
                issue="unmatched_source",
                match_field=identifiers[0][0],
                match_value=identifiers[0][1],
                qa_note="No attachment matched this source row.",
            )
            qa_count += 1

    linked_attachment_ids = {
        row[0] for row in db.execute("SELECT DISTINCT attachment_id FROM bundle_attachment_links")
    }
    for row in db.execute(
        "SELECT id, project_number, upload_id, document_id, document_number FROM bundle_attachments"
    ):
        if row[0] in linked_attachment_ids:
            continue
        insert_attachment_link_qa(
            db,
            project_number=row[1],
            attachment_id=row[0],
            issue="unmatched_attachment",
            match_field="upload_id",
            match_value=row[2] or row[3] or row[4] or "",
            qa_note="No normalized bundle row matched this attachment.",
        )
        qa_count += 1
    return {
        "bundle_attachment_links": link_count,
        "bundle_attachment_link_qa": qa_count,
    }


def insert_bundle_documents(
    db: sqlite3.Connection,
    *,
    project_number: str,
    project_id: str,
    entries: list[dict[str, Any]],
) -> tuple[int, dict[tuple[str, str], dict[str, Any]]]:
    """Insert document-like rows from loaded bundle section JSON."""
    count = 0
    parent_summary: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in entries:
        parent = document_parent_for_section(entry["name"])
        if parent is None:
            continue
        parent_kind, parent_id, default_role = parent
        for row_index, role, item in iter_document_items(
            entry["payload"], default_role
        ):
            document_id = first_text(item, "documentId", "id")
            upload_id = upload_id_from_item(item)
            db.execute(
                """
                INSERT INTO bundle_documents
                  (project_number, project_id, source_section, source_json_path,
                   source_local_path, source_row_index, parent_kind, parent_id,
                   document_role, document_id, document_number, document_type,
                   document_type_id, document_state, title, description, file_name,
                   redacted_file_name, upload_id, upload_date, upload_date_iso,
                   key_document, is_historic, stage_uploaded, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_number,
                    project_id,
                    entry["name"],
                    entry["source_json_path"],
                    entry["source_local_path"],
                    row_index,
                    parent_kind,
                    parent_id,
                    role,
                    document_id,
                    text_value(item.get("documentNumber")),
                    text_value(item.get("documentType")),
                    text_value(item.get("documentTypeId")),
                    text_value(item.get("documentState")),
                    first_text(item, "title", "description", "fileName"),
                    text_value(item.get("description")),
                    first_text(item, "fileName", "originalFilename"),
                    text_value(item.get("redactedFileName")),
                    upload_id,
                    text_value(
                        item.get("uploadDate") or item.get("redactedUploadDate")
                    ),
                    date_iso_value(
                        item.get("uploadDate") or item.get("redactedUploadDate")
                    ),
                    bool_int(item.get("keyDocument")),
                    bool_int(item.get("isHistoric")),
                    text_value(item.get("stageUploaded")),
                    compact_json(item),
                ),
            )
            add_parent_document_summary(
                parent_summary,
                parent_kind,
                parent_id,
                document_id=document_id,
                upload_id=upload_id,
            )
            count += 1
    return count, parent_summary


def insert_bundle_comments(
    db: sqlite3.Connection,
    *,
    project_number: str,
    project_id: str,
    entries: list[dict[str, Any]],
    parent_summary: dict[tuple[str, str], dict[str, Any]],
) -> int:
    """Insert public comment rows from loaded bundle section JSON."""
    count = 0
    for entry in entries:
        if entry["name"] != "comments" or not isinstance(entry["payload"], list):
            continue
        for row_index, item in enumerate(entry["payload"]):
            if not isinstance(item, dict):
                continue
            comment_id = text_value(item.get("commentId"))
            document_count, upload_ids, document_ids = parent_document_values(
                parent_summary, "comment", comment_id
            )
            first_name = text_value(item.get("firstName"))
            last_name = text_value(item.get("lastName"))
            submitter_name = " ".join(
                part for part in (first_name, last_name) if part
            ).strip()
            submitted_date = item.get("submittedDate")
            db.execute(
                """
                INSERT INTO bundle_comments
                  (project_number, project_id, source_section, source_json_path,
                   source_local_path, source_row_index, comment_id, document_number,
                   submitter_name, first_name, last_name, redacted_comment,
                   stage_uploaded, submitted_date, submitted_date_iso,
                   document_count, upload_ids, document_ids, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_number,
                    project_id,
                    entry["name"],
                    entry["source_json_path"],
                    entry["source_local_path"],
                    row_index,
                    comment_id,
                    text_value(item.get("documentNumber")),
                    submitter_name,
                    first_name,
                    last_name,
                    text_value(item.get("redactedComment")),
                    text_value(item.get("stageUploaded")),
                    text_value(submitted_date),
                    date_iso_value(submitted_date),
                    document_count,
                    upload_ids,
                    document_ids,
                    compact_json(item),
                ),
            )
            count += 1
    return count


def insert_bundle_activity_events(
    db: sqlite3.Connection,
    *,
    project_number: str,
    project_id: str,
    entries: list[dict[str, Any]],
) -> int:
    """Insert activity feed rows from loaded bundle section JSON."""
    count = 0
    for entry in entries:
        if entry["name"] != "activity_feed" or not isinstance(entry["payload"], list):
            continue
        for row_index, item in enumerate(entry["payload"]):
            if not isinstance(item, dict):
                continue
            documents = item.get("documents")
            document_count = len(documents) if isinstance(documents, list) else 0
            activity_date = item.get("activityDate")
            db.execute(
                """
                INSERT INTO bundle_activity_events
                  (project_number, project_id, source_section, source_json_path,
                   source_local_path, source_row_index, activity_date,
                   activity_date_iso, activity_date_formatted, message_text,
                   linked_document_ids, document_count, secondary_sort, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_number,
                    text_value(item.get("projectId") or project_id),
                    entry["name"],
                    entry["source_json_path"],
                    entry["source_local_path"],
                    row_index,
                    text_value(activity_date),
                    date_iso_value(activity_date),
                    text_value(item.get("activityDateTimeFormatted"))
                    or text_value(item.get("activityDateFormatted")),
                    message_text(item.get("message")),
                    linked_document_ids(item),
                    document_count,
                    int_value(item.get("secondarySort")),
                    compact_json(item),
                ),
            )
            count += 1
    return count


def insert_bundle_notes(
    db: sqlite3.Connection,
    *,
    project_number: str,
    project_id: str,
    entries: list[dict[str, Any]],
    parent_summary: dict[tuple[str, str], dict[str, Any]],
) -> int:
    """Insert note rows from loaded bundle section JSON."""
    count = 0
    for entry in entries:
        if entry["name"] != "notes" or not isinstance(entry["payload"], list):
            continue
        for row_index, item in enumerate(entry["payload"]):
            if not isinstance(item, dict):
                continue
            note_id = text_value(item.get("noteId"))
            document_count, upload_ids, document_ids = parent_document_values(
                parent_summary, "note", note_id
            )
            published_date = item.get("publishedDate")
            upload_date = item.get("uploadDate")
            db.execute(
                """
                INSERT INTO bundle_notes
                  (project_number, project_id, source_section, source_json_path,
                   source_local_path, source_row_index, note_id, document_number,
                   title, note_html, note_state, stage_uploaded, published_date,
                   published_date_iso, upload_date, upload_date_iso, uploaded_by,
                   document_count, upload_ids, document_ids, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_number,
                    text_value(item.get("projectId") or project_id),
                    entry["name"],
                    entry["source_json_path"],
                    entry["source_local_path"],
                    row_index,
                    note_id,
                    text_value(item.get("documentNumber")),
                    text_value(item.get("title")),
                    text_value(item.get("note")),
                    text_value(item.get("noteState")),
                    text_value(item.get("stageUploaded")),
                    text_value(published_date),
                    date_iso_value(published_date),
                    text_value(upload_date),
                    date_iso_value(upload_date),
                    text_value(item.get("uploadedBy")),
                    document_count,
                    upload_ids,
                    document_ids,
                    compact_json(item),
                ),
            )
            count += 1
    return count


def insert_bundle_simplified_information_requests(
    db: sqlite3.Connection,
    *,
    project_number: str,
    project_id: str,
    entries: list[dict[str, Any]],
    parent_summary: dict[tuple[str, str], dict[str, Any]],
) -> int:
    """Insert simplified information request rows from bundle section JSON."""
    count = 0
    for entry in entries:
        if (
            entry["name"] != "simplified_information_requests"
            or not isinstance(entry["payload"], list)
        ):
            continue
        for row_index, item in enumerate(entry["payload"]):
            if not isinstance(item, dict):
                continue
            request_id = first_text(
                item, "simplifiedInformationRequestId", "informationRequestId", "id"
            )
            document_count, upload_ids, document_ids = parent_document_values(
                parent_summary, "simplified_information_request", request_id
            )
            status_date = item.get("statusDate")
            published_date = item.get("publishedDate")
            answered_date = item.get("answeredDate")
            db.execute(
                """
                INSERT INTO bundle_simplified_information_requests
                  (project_number, project_id, source_section, source_json_path,
                   source_local_path, source_row_index, request_id, request_number,
                   status, status_date, status_date_iso, published_date,
                   published_date_iso, answered_date, answered_date_iso,
                   project_stage, request_document_number, response_document_number,
                   document_count, upload_ids, document_ids, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_number,
                    project_id,
                    entry["name"],
                    entry["source_json_path"],
                    entry["source_local_path"],
                    row_index,
                    request_id,
                    int_value(item.get("number")),
                    text_value(item.get("status")),
                    text_value(status_date),
                    date_iso_value(status_date),
                    text_value(published_date),
                    date_iso_value(published_date),
                    text_value(answered_date),
                    date_iso_value(answered_date),
                    text_value(item.get("projectStage")),
                    text_value(item.get("documentNumberRequest")),
                    text_value(item.get("documentNumberResponse")),
                    document_count,
                    upload_ids,
                    document_ids,
                    compact_json(item),
                ),
            )
            count += 1
    return count


def document_ids_from_email(item: dict[str, Any]) -> str:
    """Return document IDs listed directly on an email row."""
    documents = item.get("documents")
    if not isinstance(documents, list):
        return ""
    values: list[str] = []
    for document in documents:
        if isinstance(document, dict):
            values.append(first_text(document, "documentId", "id", "documentNumber"))
    return unique_csv(values)


def upload_ids_from_email(item: dict[str, Any]) -> str:
    """Return upload IDs listed directly on an email row."""
    documents = item.get("documents")
    if not isinstance(documents, list):
        return ""
    values: list[str] = []
    for document in documents:
        if isinstance(document, dict):
            values.append(upload_id_from_item(document))
    return unique_csv(values)


def insert_bundle_emails(
    db: sqlite3.Connection,
    *,
    project_number: str,
    project_id: str,
    entries: list[dict[str, Any]],
    parent_summary: dict[tuple[str, str], dict[str, Any]],
) -> int:
    """Insert email/notification rows from loaded bundle section JSON."""
    count = 0
    seen_email_ids: set[str] = set()
    for entry in entries:
        name = entry["name"]
        if name != "emails" and not name.startswith("emails/"):
            continue
        for row_index, item in enumerate(email_items(entry["payload"])):
            email_id = text_value(item.get("emailMessageId"))
            if email_id and email_id in seen_email_ids:
                continue
            if email_id:
                seen_email_ids.add(email_id)
            documents = item.get("documents")
            direct_document_count = len(documents) if isinstance(documents, list) else 0
            summary_count, summary_upload_ids, summary_document_ids = (
                parent_document_values(parent_summary, "email", email_id)
            )
            sent_date = item.get("sentDate")
            db.execute(
                """
                INSERT INTO bundle_emails
                  (project_number, project_id, source_section, source_json_path,
                   source_local_path, source_row_index, email_message_id, email_date,
                   sent_date, sent_date_iso, subject, message_type, message_type_id,
                   recipient_type, recipient_type_id, stage_uploaded, content,
                   document_count, upload_ids, document_ids, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_number,
                    project_id,
                    name,
                    entry["source_json_path"],
                    entry["source_local_path"],
                    row_index,
                    email_id,
                    text_value(item.get("emailDate")),
                    text_value(sent_date),
                    date_iso_value(sent_date),
                    text_value(item.get("subject")),
                    text_value(item.get("emailMessageType")),
                    text_value(item.get("emailMessageTypeId")),
                    text_value(item.get("emailRecipientType")),
                    text_value(item.get("emailRecipientTypeId")),
                    text_value(item.get("stageUploaded")),
                    text_value(item.get("content")),
                    direct_document_count or summary_count,
                    upload_ids_from_email(item) or summary_upload_ids,
                    document_ids_from_email(item) or summary_document_ids,
                    compact_json(item),
                ),
            )
            count += 1
    return count


def insert_bundles(db: sqlite3.Connection, bundle_root: Path | None) -> dict[str, int]:
    """Insert locally downloaded project bundle manifest summaries."""
    resolved_bundle_root = (
        repo_path(bundle_root).resolve() if bundle_root is not None else None
    )
    bundle_count = 0
    section_count = 0
    attachment_count = 0
    document_count = 0
    comment_count = 0
    activity_count = 0
    note_count = 0
    simplified_information_request_count = 0
    email_count = 0
    for manifest_path, manifest in iter_bundle_manifests(bundle_root):
        project_number = text_value(manifest.get("projectNumber"))
        project_id = text_value(manifest.get("projectId"))
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
                project_id,
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

        entries = bundle_section_entries(bundle_dir, manifest.get("sections"))
        for entry in entries:
            section = entry["section"]
            db.execute(
                """
                INSERT INTO bundle_sections
                  (project_number, name, endpoint, local_path, row_count)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    project_number,
                    entry["name"],
                    entry["endpoint"],
                    entry["source_local_path"],
                    int_value(section.get("count")),
                ),
            )
            section_count += 1

        current_document_count, parent_summary = insert_bundle_documents(
            db,
            project_number=project_number,
            project_id=project_id,
            entries=entries,
        )
        document_count += current_document_count
        comment_count += insert_bundle_comments(
            db,
            project_number=project_number,
            project_id=project_id,
            entries=entries,
            parent_summary=parent_summary,
        )
        activity_count += insert_bundle_activity_events(
            db,
            project_number=project_number,
            project_id=project_id,
            entries=entries,
        )
        note_count += insert_bundle_notes(
            db,
            project_number=project_number,
            project_id=project_id,
            entries=entries,
            parent_summary=parent_summary,
        )
        simplified_information_request_count += (
            insert_bundle_simplified_information_requests(
                db,
                project_number=project_number,
                project_id=project_id,
                entries=entries,
                parent_summary=parent_summary,
            )
        )
        email_count += insert_bundle_emails(
            db,
            project_number=project_number,
            project_id=project_id,
            entries=entries,
            parent_summary=parent_summary,
        )

        attachments = manifest.get("attachments")
        if isinstance(attachments, list):
            for attachment in attachments:
                if not isinstance(attachment, dict):
                    continue
                attachment_path = text_value(attachment.get("path"))
                link_fields = bundle_attachment_link_fields(
                    resolved_bundle_root,
                    bundle_dir,
                    attachment_path,
                )
                db.execute(
                    """
                    INSERT INTO bundle_attachments
                      (project_number, document_number, document_type, source_kind,
                       source_path, description, file_name, local_path, bundle_path, datasette_url,
                       bytes, content_type,
                       downloaded, timestamp_iso, upload_id, document_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project_number,
                        text_value(attachment.get("documentNumber")),
                        text_value(attachment.get("documentType")),
                        text_value(attachment.get("sourceKind")),
                        text_value(attachment.get("sourcePath")),
                        text_value(attachment.get("description")),
                        text_value(
                            attachment.get("fileName")
                            or attachment.get("originalFilename")
                            or attachment.get("redactedFileName")
                        ),
                        link_fields["local_path"],
                        link_fields["bundle_path"],
                        link_fields["datasette_url"],
                        int_value(attachment.get("bytes")),
                        text_value(attachment.get("contentType")),
                        bool_int(attachment.get("downloaded")),
                        text_value(attachment.get("timestampIso")),
                        text_value(attachment.get("uploadId")),
                        text_value(attachment.get("documentId")),
                    ),
                )
                attachment_count += 1

    counts = {
        "project_bundles": bundle_count,
        "bundle_sections": section_count,
        "bundle_attachments": attachment_count,
        "bundle_documents": document_count,
        "bundle_comments": comment_count,
        "bundle_activity_events": activity_count,
        "bundle_notes": note_count,
        "bundle_simplified_information_requests": (
            simplified_information_request_count
        ),
        "bundle_emails": email_count,
    }
    counts.update(insert_bundle_attachment_links(db))
    return counts


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
    cluster_map_config = {
        "tile_layer": YUKON_BASEMAP_TILE_LAYER,
        "tile_layer_options": YUKON_BASEMAP_TILE_OPTIONS,
    }
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
        "plugins": {
            "datasette-cluster-map": cluster_map_config,
        },
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
                                **cluster_map_config,
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
                    "bundle_attachment_links": {
                        "facets": ["project_number", "source_table", "match_field"]
                    },
                    "bundle_attachment_link_qa": {
                        "facets": ["project_number", "issue", "source_table"]
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
                                   source_kind, description, file_name, datasette_url,
                                   local_path, bytes, timestamp_iso
                              FROM bundle_attachments
                             WHERE downloaded = 1
                             ORDER BY project_number DESC, document_number
                        """,
                    },
                    "linked_bundle_attachments": {
                        "title": "Bundle records linked to local attachments",
                        "sql": """
                            SELECT project_number, source_table, source_row_id,
                                   document_number, match_field, datasette_url,
                                   local_path
                              FROM bundle_attachment_links
                             ORDER BY project_number DESC, source_table, source_row_id
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
    metadata_path = repo_path(
        args.metadata_output or default_metadata_path(output_path)
    )
    bundle_root = None if args.no_bundles else repo_path(args.bundle_root)
    counts = build_explorer(
        output_path,
        metadata_output=metadata_path,
        api_cache_path=args.api_cache,
        bundle_root=bundle_root,
        include_map_features=not args.no_map_features,
    )
    print(f"Wrote {output_path}")
    print(f"Wrote {metadata_path}")
    for item, count in counts.items():
        print(f"  {item}: {count}")
    print()
    print("Run with Datasette, for example:")
    command = (
        "  uvx --with datasette-cluster-map datasette "
        f"{output_path} -m {metadata_path} --plugins-dir {ROOT / 'datasette_plugins'}"
    )
    if bundle_root is not None:
        command += f" --static bundles:{bundle_root}"
    print(command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
