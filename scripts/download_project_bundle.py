"""
Download a public YESAB Registry project page bundle.

The YESAB Registry web app does not expose one public "download everything"
endpoint. It renders a project page by calling several JSON endpoints and by
downloading public, redacted uploads one at a time. This script mirrors that
behavior into a local directory:

- JSON section payloads are written under ``json/``
- public attachments are written under ``attachments/``
- ``manifest.json`` records endpoints, original filenames, local paths, and
  download status

By default only public ``redactedUploadId`` attachments are downloaded. If you
have a valid Registry JWT and need the authenticated download behavior used by
the browser app, pass it with ``--upload-token``.

(c)2026 Matt Wilkie, Yukon Government. MIT License.
"""

# /// script
# requires-python = ">=3.14"
# ///
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_BASE_URL = "https://yesabregistry.ca"
DEFAULT_OUTPUT_ROOT = BASE_DIR / "out" / "project-bundles"
TIMEOUT = 60
DATE_FIELD_PRECEDENCE = (
    "receivedDate",
    "uploadDate",
    "redactedUploadDate",
    "submittedDate",
    "dateSent",
    "sentDate",
)
DATE_VALUE_MIN_EPOCH_MS = 24 * 60 * 60 * 1000


class BundleError(RuntimeError):
    """Raised when a project bundle cannot be fetched or written."""


@dataclass(frozen=True)
class SectionSpec:
    """One top-level project page API section."""

    name: str
    path_template: str
    optional: bool = True


SECTION_SPECS: tuple[SectionSpec, ...] = (
    SectionSpec("meta", "/api/projects/{project_id}/meta"),
    SectionSpec("documents", "/api/projects/{project_id}/documents"),
    SectionSpec("key_documents", "/api/projects/{project_id}/key-documents"),
    SectionSpec("notes", "/api/projects/{project_id}/notes"),
    SectionSpec("correspondence", "/api/projects/{project_id}/correspondence"),
    SectionSpec(
        "correspondence_documents",
        "/api/projects/{project_id}/correspondence-documents",
    ),
    SectionSpec("document_groups", "/api/projects/{project_id}/document-groups"),
    SectionSpec(
        "information_requests",
        "/api/projects/{project_id}/information-requests",
    ),
    SectionSpec(
        "simplified_information_requests",
        "/api/projects/{project_id}/simplified-information-requests",
    ),
    SectionSpec("activity_feed", "/api/projects/{project_id}/activity-feed"),
    SectionSpec("comments", "/api/projects/{project_id}/comments"),
    SectionSpec("emails", "/api/projects/{project_id}/emails"),
    SectionSpec("hearings", "/api/projects/{project_id}/hearings"),
    SectionSpec("intervenors", "/api/projects/{project_id}/intervenors"),
)


def utc_now_iso() -> str:
    """Return the current UTC timestamp in ISO-8601 format."""
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def project_id_from_ref(project_ref: str) -> str:
    """Extract a project ID-like value from a Registry URL or raw identifier."""
    value = project_ref.strip()
    if not value:
        raise BundleError("Project reference is required")

    parsed = urllib.parse.urlparse(value)
    if parsed.scheme and parsed.netloc:
        parts = [part for part in parsed.path.split("/") if part]
        if "projects" in parts:
            index = parts.index("projects")
            if index + 1 < len(parts):
                return urllib.parse.unquote(parts[index + 1])
        raise BundleError(f"Could not find /projects/<id> in URL: {project_ref}")

    return value.strip("/")


def quote_path_segment(value: str) -> str:
    """Quote one URL path segment."""
    return urllib.parse.quote(value, safe="")


class RegistryClient:
    """Small urllib-backed client for the YESAB Registry JSON/upload APIs."""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: int = TIMEOUT,
        auth_token: str = "",
        upload_token: str = "",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.auth_token = auth_token
        self.upload_token = upload_token

    def url_for(self, path: str) -> str:
        """Return an absolute URL for one API path."""
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return f"{self.base_url}/{path.lstrip('/')}"

    def request_headers(self, *, accept: str) -> dict[str, str]:
        """Return default request headers."""
        headers = {
            "Accept": accept,
            "User-Agent": "yesab-project-bundler/1.0",
        }
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        return headers

    def get_json(self, path: str, *, optional: bool = False) -> object:
        """Fetch one JSON API path."""
        url = self.url_for(path)
        request = urllib.request.Request(
            url,
            headers=self.request_headers(accept="application/json, text/plain, */*"),
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            if optional and exc.code in {401, 403, 404}:
                return []
            message = exc.read().decode("utf-8", errors="replace")
            raise BundleError(f"GET {url} failed with HTTP {exc.code}: {message}") from exc
        except urllib.error.URLError as exc:
            raise BundleError(f"GET {url} failed: {exc}") from exc

        if not raw.strip():
            return [] if optional else {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise BundleError(f"GET {url} returned invalid JSON: {exc}") from exc

    def download_upload(self, upload_id: str) -> tuple[bytes, dict[str, str]]:
        """Download one upload by ID and return bytes plus response metadata."""
        path = f"/api/uploads/{quote_path_segment(upload_id)}"
        if self.upload_token:
            path = f"{path}/{quote_path_segment(self.upload_token)}"
        url = self.url_for(path)
        request = urllib.request.Request(
            url,
            headers=self.request_headers(accept="application/octet-stream, */*"),
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read()
                headers = {
                    "content_type": response.headers.get("Content-Type", ""),
                    "content_disposition": response.headers.get(
                        "Content-Disposition", ""
                    ),
                    "content_length": response.headers.get("Content-Length", ""),
                }
                return body, headers
        except urllib.error.HTTPError as exc:
            message = exc.read().decode("utf-8", errors="replace")
            raise BundleError(
                f"GET {url} failed with HTTP {exc.code}: {message}"
            ) from exc
        except urllib.error.URLError as exc:
            raise BundleError(f"GET {url} failed: {exc}") from exc


def as_list(value: object) -> list[object]:
    """Return value if it is a list, otherwise an empty list."""
    return value if isinstance(value, list) else []


def object_count(value: object) -> int | None:
    """Return a useful count for a section payload."""
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        return len(value)
    return None


def write_json(path: Path, payload: object) -> None:
    """Write a JSON payload with stable formatting."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )


def section_file(output_dir: Path, section_name: str) -> Path:
    """Return the JSON file path for a section name."""
    return output_dir / "json" / f"{section_name}.json"


def fetch_section(
    *,
    client: RegistryClient,
    output_dir: Path,
    payloads: dict[str, object],
    sections: list[dict[str, object]],
    project_id: str,
    name: str,
    path: str,
    optional: bool,
) -> object:
    """Fetch, write, and record one section payload."""
    payload = client.get_json(path, optional=optional)
    local_path = section_file(output_dir, name)
    write_json(local_path, payload)
    payloads[name] = payload
    sections.append(
        {
            "name": name,
            "endpoint": path,
            "path": str(local_path.relative_to(output_dir)),
            "count": object_count(payload),
        }
    )
    return payload


def collect_ids(payload: object, id_key: str) -> list[str]:
    """Collect unique string IDs from nested API payloads."""
    ids: list[str] = []
    seen: set[str] = set()

    def visit(value: object) -> None:
        if isinstance(value, dict):
            candidate = value.get(id_key)
            if isinstance(candidate, str) and candidate and candidate not in seen:
                seen.add(candidate)
                ids.append(candidate)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(payload)
    return ids


def ref_filename(value: dict[str, object]) -> str:
    """Return the best visible filename from one document-like JSON object."""
    for key in (
        "fileName",
        "redactedFileName",
        "name",
        "title",
        "description",
        "documentNumber",
    ):
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return ""


def source_kind_from_path(path: str) -> str:
    """Return a stable source bucket name for one nested upload JSON path."""
    if path.startswith("$.comments"):
        return "comments"
    if path.startswith("$.documents"):
        return "documents"
    if path.startswith("$.emails"):
        return "emails"
    if path.startswith("$.activity_feed"):
        return "activity"
    if path.startswith("$.notes"):
        return "notes"
    if path.startswith("$.correspondence"):
        return "correspondence"
    if path.startswith("$.document_groups"):
        return "document_groups"
    if path.startswith("$.information_requests"):
        return "information_requests"
    if path.startswith("$.simplified_information_requests"):
        return "simplified_information_requests"
    if path.startswith("$.hearings"):
        return "hearings"
    if path.startswith("$.intervenors"):
        return "intervenors"
    return "unknown"


def collect_upload_refs(payload: object, *, public_only: bool = True) -> list[dict[str, str]]:
    """Collect upload references from nested API payloads.

    The browser uses ``redactedUploadId`` for public downloads. Authenticated
    users may see ``unredactedUploadId`` values too; those are ignored unless
    ``public_only`` is false.
    """
    refs: list[dict[str, str]] = []
    seen: set[str] = set()

    def visit(value: object, path: str) -> None:
        if isinstance(value, dict):
            candidates: list[tuple[str, str]] = []
            redacted = value.get("redactedUploadId")
            if isinstance(redacted, str) and redacted.strip():
                candidates.append(("redactedUploadId", redacted.strip()))
            if not public_only:
                for key, child in value.items():
                    if key.lower().endswith("uploadid") and isinstance(child, str):
                        child_value = child.strip()
                        if child_value and (key, child_value) not in candidates:
                            candidates.append((key, child_value))

            for upload_key, upload_id in candidates:
                if upload_id in seen:
                    continue
                seen.add(upload_id)
                ref = {
                    "uploadId": upload_id,
                    "uploadIdKey": upload_key,
                    "sourcePath": path,
                    "sourceKind": source_kind_from_path(path),
                }
                for key in (
                    "documentId",
                    "documentNumber",
                    "commentId",
                    "emailMessageId",
                    "documentType",
                    "documentTypeId",
                    "description",
                    "fileName",
                    "redactedFileName",
                    *DATE_FIELD_PRECEDENCE,
                ):
                    child = value.get(key)
                    if child is not None:
                        ref[key] = str(child)
                filename = ref_filename(value)
                if filename:
                    ref["originalFilename"] = filename
                refs.append(ref)

            for key, child in value.items():
                visit(child, f"{path}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{path}[{index}]")

    visit(payload, "$")
    return refs


def filename_from_content_disposition(value: str) -> str:
    """Extract a filename from a Content-Disposition header."""
    if not value:
        return ""
    match = re.search(r"filename\*=UTF-8''([^;]+)", value, flags=re.IGNORECASE)
    if match:
        return urllib.parse.unquote(match.group(1).strip().strip('"'))
    match = re.search(r'filename="([^"]+)"', value, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip()
    match = re.search(r"filename=([^;]+)", value, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip().strip('"')
    return ""


def safe_filename(value: str, *, fallback: str) -> str:
    """Return an ASCII-only filename safe for local artifact paths."""
    name = value.strip() or fallback
    normalized = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    normalized = re.sub(r"[^A-Za-z0-9._ -]+", "_", normalized)
    normalized = re.sub(r"\s+", "_", normalized)
    normalized = re.sub(r"_+", "_", normalized)
    normalized = normalized.strip(" ._-")
    if not normalized:
        normalized = fallback
    return normalized[:180]


def ensure_extension(filename: str, content_type: str) -> str:
    """Add an extension from content type when the filename has none."""
    if Path(filename).suffix:
        return filename
    extension = mimetypes.guess_extension(content_type.split(";", maxsplit=1)[0].strip())
    if extension:
        return f"{filename}{extension}"
    return filename


def unique_path(directory: Path, filename: str, used: set[str]) -> Path:
    """Return a unique path in a directory."""
    path = directory / filename
    stem = path.stem
    suffix = path.suffix
    counter = 2
    while path.name.lower() in used or path.exists():
        path = directory / f"{stem}-{counter}{suffix}"
        counter += 1
    used.add(path.name.lower())
    return path


def normalized_document_number(document_number: str, project_number: str) -> str:
    """Return a full project document number when possible."""
    document_number = document_number.strip()
    project_number = project_number.strip()
    if not document_number:
        return ""
    if project_number and document_number.startswith(f"{project_number}-"):
        return document_number
    if project_number and re.fullmatch(r"\d{4}", document_number):
        return f"{project_number}-{document_number}"
    return document_number


def filename_prefix(ref: dict[str, str], project_number: str) -> str:
    """Return the preferred local filename prefix for one attachment."""
    document_number = normalized_document_number(
        ref.get("documentNumber", ""),
        project_number,
    )
    prefix = document_number or ref.get("documentId", "") or ref["uploadId"]
    if ref.get("sourceKind") == "comments":
        prefix = f"{prefix}_cmt"
    return prefix


def attachment_filename(
    ref: dict[str, str],
    headers: dict[str, str],
    *,
    used: set[str],
    project_number: str = "",
) -> Path:
    """Return a unique ASCII local filename for one attachment."""
    header_name = filename_from_content_disposition(headers.get("content_disposition", ""))
    visible_name = ref.get("originalFilename") or ref.get("fileName") or header_name
    if not visible_name:
        visible_name = ref["uploadId"]
    prefix = filename_prefix(ref, project_number)
    safe_prefix = safe_filename(prefix, fallback=ref["uploadId"])
    safe_visible = safe_filename(visible_name, fallback=ref["uploadId"])
    if not safe_visible.lower().startswith(safe_prefix.lower()):
        safe_visible = f"{safe_prefix}_{safe_visible}"
    safe_visible = ensure_extension(safe_visible, headers.get("content_type", ""))
    return unique_path(Path(), safe_visible, used)


def epoch_ms_from_ref(ref: dict[str, str]) -> tuple[str, int | None]:
    """Return the preferred timestamp field and epoch milliseconds from a ref."""
    for field in DATE_FIELD_PRECEDENCE:
        value = ref.get(field)
        if value is None:
            continue
        try:
            epoch_ms = int(float(value))
        except ValueError:
            continue
        if epoch_ms >= DATE_VALUE_MIN_EPOCH_MS:
            return field, epoch_ms
    return "", None


def date_metadata_from_ref(ref: dict[str, str]) -> dict[str, object]:
    """Return manifest timestamp metadata derived from one upload ref."""
    field, epoch_ms = epoch_ms_from_ref(ref)
    if epoch_ms is None:
        return {
            "timestampField": "",
            "timestampEpochMs": None,
            "timestampIso": "",
            "timestampApplied": False,
        }
    timestamp = datetime.fromtimestamp(epoch_ms / 1000, UTC)
    return {
        "timestampField": field,
        "timestampEpochMs": epoch_ms,
        "timestampIso": timestamp.replace(microsecond=0).isoformat().replace(
            "+00:00",
            "Z",
        ),
        "timestampApplied": False,
    }


def apply_attachment_timestamp(path: Path, metadata: dict[str, object]) -> None:
    """Set a downloaded attachment's mtime from Registry date metadata."""
    epoch_ms = metadata.get("timestampEpochMs")
    if not isinstance(epoch_ms, int):
        return
    timestamp = epoch_ms / 1000
    os.utime(path, (timestamp, timestamp))
    metadata["timestampApplied"] = True


def download_attachments(
    *,
    client: RegistryClient,
    output_dir: Path,
    upload_refs: list[dict[str, str]],
    project_number: str = "",
) -> tuple[list[dict[str, object]], list[dict[str, str]]]:
    """Download upload references into attachments/."""
    attachments_dir = output_dir / "attachments"
    attachments_dir.mkdir(parents=True, exist_ok=True)
    attachments: list[dict[str, object]] = []
    errors: list[dict[str, str]] = []
    used_names: set[str] = set()

    for ref in upload_refs:
        upload_id = ref["uploadId"]
        try:
            body, headers = client.download_upload(upload_id)
            relative_name = attachment_filename(
                ref,
                headers,
                used=used_names,
                project_number=project_number,
            )
            attachment_path = attachments_dir / relative_name
            attachment_path.write_bytes(body)
            date_metadata = date_metadata_from_ref(ref)
            apply_attachment_timestamp(attachment_path, date_metadata)
            attachments.append(
                {
                    **ref,
                    "path": str(attachment_path.relative_to(output_dir)),
                    "bytes": len(body),
                    "contentType": headers.get("content_type", ""),
                    "contentDisposition": headers.get("content_disposition", ""),
                    "downloaded": True,
                    **date_metadata,
                }
            )
        except BundleError as exc:
            errors.append({"uploadId": upload_id, "error": str(exc)})
            attachments.append(
                {
                    **ref,
                    "downloaded": False,
                    "error": str(exc),
                    **date_metadata_from_ref(ref),
                }
            )

    return attachments, errors


def fetch_project_details(
    *,
    client: RegistryClient,
    output_dir: Path,
    payloads: dict[str, object],
    sections: list[dict[str, object]],
    project_ref_id: str,
) -> tuple[str, dict[str, object]]:
    """Fetch details and resolve a project-number lookup to the real project ID."""
    encoded_ref = quote_path_segment(project_ref_id)
    details = fetch_section(
        client=client,
        output_dir=output_dir,
        payloads=payloads,
        sections=sections,
        project_id=project_ref_id,
        name="details",
        path=f"/api/projects/{encoded_ref}",
        optional=False,
    )
    if not isinstance(details, dict):
        raise BundleError("Project details response was not a JSON object")

    project = details.get("project")
    if isinstance(project, dict):
        return str(project.get("projectId") or project_ref_id), project

    resolved_project_id = details.get("projectId")
    if isinstance(resolved_project_id, str) and resolved_project_id:
        resolved_details = fetch_section(
            client=client,
            output_dir=output_dir,
            payloads=payloads,
            sections=sections,
            project_id=resolved_project_id,
            name="details_resolved",
            path=f"/api/projects/{quote_path_segment(resolved_project_id)}",
            optional=False,
        )
        if isinstance(resolved_details, dict) and isinstance(
            resolved_details.get("project"), dict
        ):
            write_json(section_file(output_dir, "details"), resolved_details)
            payloads["details"] = resolved_details
            return resolved_project_id, resolved_details["project"]  # type: ignore[index]

    raise BundleError("Project details response did not include a project object")


def fetch_nested_sections(
    *,
    client: RegistryClient,
    output_dir: Path,
    payloads: dict[str, object],
    sections: list[dict[str, object]],
    project_id: str,
) -> None:
    """Fetch per-item document/detail endpoints used by project page tabs."""
    comments = payloads.get("comments", [])
    for comment in as_list(comments):
        if isinstance(comment, dict) and isinstance(comment.get("commentId"), str):
            comment_id = comment["commentId"]
            fetch_section(
                client=client,
                output_dir=output_dir,
                payloads=payloads,
                sections=sections,
                project_id=project_id,
                name=f"comments/{comment_id}_documents",
                path=(
                    f"/api/projects/{quote_path_segment(project_id)}/comments/"
                    f"{quote_path_segment(comment_id)}/documents"
                ),
                optional=True,
            )

    for email_id in collect_ids(payloads.get("emails", []), "emailMessageId"):
        fetch_section(
            client=client,
            output_dir=output_dir,
            payloads=payloads,
            sections=sections,
            project_id=project_id,
            name=f"emails/{email_id}",
            path=(
                f"/api/projects/{quote_path_segment(project_id)}/emails/"
                f"{quote_path_segment(email_id)}"
            ),
            optional=True,
        )

    nested_specs = (
        ("notes", "noteId", "notes/{item_id}", "notes/{item_id}/documents"),
        (
            "correspondence",
            "correspondenceId",
            "correspondence/{item_id}",
            "correspondence/{item_id}/documents",
        ),
        (
            "document_groups",
            "documentGroupId",
            "document_groups/{item_id}",
            "document-groups/{item_id}/documents",
        ),
        (
            "information_requests",
            "informationRequestId",
            "information_requests/{item_id}",
            "information-requests/{item_id}/documents",
        ),
        (
            "simplified_information_requests",
            "simplifiedInformationRequestId",
            "simplified_information_requests/{item_id}",
            "simplified-information-requests/{item_id}/documents",
        ),
        ("hearings", "hearingId", "hearings/{item_id}", "hearings/{item_id}/documents"),
        ("intervenors", "intervenorId", "", "intervenors/{item_id}/documents"),
    )
    for section_name, id_key, detail_template, docs_template in nested_specs:
        for item_id in collect_ids(payloads.get(section_name, []), id_key):
            if detail_template:
                fetch_section(
                    client=client,
                    output_dir=output_dir,
                    payloads=payloads,
                    sections=sections,
                    project_id=project_id,
                    name=detail_template.format(item_id=item_id),
                    path=(
                        f"/api/projects/{quote_path_segment(project_id)}/"
                        f"{detail_template.format(item_id=quote_path_segment(item_id))}"
                    ),
                    optional=True,
                )
            fetch_section(
                client=client,
                output_dir=output_dir,
                payloads=payloads,
                sections=sections,
                project_id=project_id,
                name=docs_template.replace("/", "_").format(item_id=item_id),
                path=(
                    f"/api/projects/{quote_path_segment(project_id)}/"
                    f"{docs_template.format(item_id=quote_path_segment(item_id))}"
                ),
                optional=True,
            )


def write_project_bundle(
    project_ref: str,
    output_dir: Path,
    *,
    client: RegistryClient | Any | None = None,
    download_files: bool = True,
    public_only: bool = True,
) -> dict[str, object]:
    """Write a YESAB project bundle directory and return its manifest."""
    project_ref_id = project_id_from_ref(project_ref)
    client = client or RegistryClient()
    output_dir.mkdir(parents=True, exist_ok=True)

    payloads: dict[str, object] = {}
    sections: list[dict[str, object]] = []
    errors: list[dict[str, str]] = []

    project_id, project = fetch_project_details(
        client=client,
        output_dir=output_dir,
        payloads=payloads,
        sections=sections,
        project_ref_id=project_ref_id,
    )
    encoded_project_id = quote_path_segment(project_id)
    for spec in SECTION_SPECS:
        fetch_section(
            client=client,
            output_dir=output_dir,
            payloads=payloads,
            sections=sections,
            project_id=project_id,
            name=spec.name,
            path=spec.path_template.format(project_id=encoded_project_id),
            optional=spec.optional,
        )

    fetch_nested_sections(
        client=client,
        output_dir=output_dir,
        payloads=payloads,
        sections=sections,
        project_id=project_id,
    )

    upload_refs = collect_upload_refs(payloads, public_only=public_only)
    if download_files:
        attachments, download_errors = download_attachments(
            client=client,
            output_dir=output_dir,
            upload_refs=upload_refs,
            project_number=str(project.get("projectNumber", "")),
        )
        errors.extend(download_errors)
    else:
        attachments = [
            {**ref, "downloaded": False, **date_metadata_from_ref(ref)}
            for ref in upload_refs
        ]

    manifest: dict[str, object] = {
        "generatedAt": utc_now_iso(),
        "sourceBaseUrl": getattr(client, "base_url", DEFAULT_BASE_URL),
        "projectRef": project_ref,
        "projectId": project_id,
        "projectNumber": project.get("projectNumber", ""),
        "title": project.get("title", ""),
        "sectionCount": len(sections),
        "attachmentCount": len(attachments),
        "downloadedAttachmentCount": sum(
            1 for item in attachments if item.get("downloaded")
        ),
        "sections": sections,
        "attachments": attachments,
        "errors": errors,
    }
    write_json(output_dir / "manifest.json", manifest)
    return manifest


def create_zip(bundle_dir: Path, zip_path: Path | None = None) -> Path:
    """Create a zip archive of a bundle directory."""
    archive_path = zip_path or bundle_dir.with_suffix(".zip")
    if archive_path.exists():
        archive_path.unlink()
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(bundle_dir.rglob("*")):
            if path.is_file() and path != archive_path:
                archive.write(path, path.relative_to(bundle_dir.parent))
    return archive_path


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "project",
        help="YESAB Registry project URL, project ID, or project number.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help=(
            "Directory to write. Defaults to "
            "out/project-bundles/<project-id-or-number>."
        ),
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"YESAB Registry base URL. Defaults to {DEFAULT_BASE_URL}.",
    )
    parser.add_argument(
        "--auth-token",
        default=os.environ.get("YESAB_REGISTRY_TOKEN", ""),
        help="Optional JWT used as a Bearer token for JSON API requests.",
    )
    parser.add_argument(
        "--upload-token",
        default=os.environ.get("YESAB_REGISTRY_UPLOAD_TOKEN", ""),
        help=(
            "Optional JWT appended to /api/uploads/<id>/<token>, matching the "
            "browser app's authenticated download behavior."
        ),
    )
    parser.add_argument(
        "--include-unredacted-upload-ids",
        action="store_true",
        help="Also collect non-redacted upload ID fields visible in JSON payloads.",
    )
    parser.add_argument(
        "--no-attachments",
        action="store_true",
        help="Only write JSON and manifest; do not download attachment bytes.",
    )
    parser.add_argument(
        "--zip",
        action="store_true",
        help="Create a zip archive next to the output directory.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=TIMEOUT,
        help=f"HTTP timeout in seconds. Defaults to {TIMEOUT}.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    args = parse_args(sys.argv[1:] if argv is None else argv)
    project_ref_id = project_id_from_ref(args.project)
    output_dir = args.output_dir or DEFAULT_OUTPUT_ROOT / safe_filename(
        project_ref_id,
        fallback="project",
    )
    client = RegistryClient(
        base_url=args.base_url,
        timeout=args.timeout,
        auth_token=args.auth_token,
        upload_token=args.upload_token or args.auth_token,
    )
    manifest = write_project_bundle(
        args.project,
        output_dir,
        client=client,
        download_files=not args.no_attachments,
        public_only=not args.include_unredacted_upload_ids,
    )
    print(f"Bundle directory : {output_dir}")
    print(f"Project          : {manifest.get('projectNumber') or manifest.get('projectId')}")
    print(f"JSON sections    : {manifest['sectionCount']}")
    print(
        "Attachments      :",
        f"{manifest['downloadedAttachmentCount']}/{manifest['attachmentCount']}",
        "downloaded",
    )
    print(f"Manifest         : {output_dir / 'manifest.json'}")
    if args.zip:
        archive_path = create_zip(output_dir)
        print(f"Zip archive      : {archive_path}")
    if manifest.get("errors"):
        print(f"Warnings/errors  : {len(manifest['errors'])}; see manifest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
