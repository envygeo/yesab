"""
Cache YESAB API responses in year buckets and emit a merged local dataset.

The cache is intentionally simple:
- refresh only the current year bucket by default
- keep older bucket snapshots on disk
- normalize all cached buckets into one merged compressed dataset

This avoids re-fetching the full registry every run while keeping recent
projects reasonably fresh for map enrichment.

(c)2026 Matt Wilkie, Yukon Government. MIT License.
"""

# /// script
# requires-python = ">=3.14"
# ///
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import compression.zstd as zstd

API_BASE = "https://yesabregistry.ca/api/integration/projects"
BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data" / "api"
BUCKET_DIR = DATA_DIR / "buckets"
STATE_FILE = DATA_DIR / "state.json"
MERGED_FILE = DATA_DIR / "projects_merged.json.zst"
TIMEOUT = 60
ZSTD_LEVEL = 10


def utc_now_iso() -> str:
    """Return the current UTC timestamp in ISO-8601 format."""
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_state() -> dict:
    """Return the persisted cache state, or an empty state if none exists."""
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"buckets": {}}


def save_state(state: dict) -> None:
    """Persist the cache state."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def bucket_key(start_year: int, end_year: int) -> str:
    """Return the stable filename/key for a year bucket."""
    return f"{start_year}-{end_year}"


def bucket_path(start_year: int, end_year: int) -> Path:
    """Return the file path for a cached year bucket."""
    return BUCKET_DIR / f"projects_{bucket_key(start_year, end_year)}.json.zst"


def build_url(start_year: int, end_year: int) -> str:
    """Build the list endpoint URL for a year bucket."""
    return f"{API_BASE}?startYear={start_year}&endYear={end_year}"


def sha256_text(text: str) -> str:
    """Return the SHA-256 hash for a UTF-8 text payload."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_zstd_json(path: Path, payload: object) -> None:
    """Write one JSON payload compressed with Zstandard."""
    with zstd.open(path, "wt", encoding="utf-8", level=ZSTD_LEVEL) as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)


def read_zstd_json(path: Path) -> dict:
    """Read one Zstandard-compressed JSON payload."""
    with zstd.open(path, "rt", encoding="utf-8") as handle:
        loaded = json.load(handle)
    if not isinstance(loaded, dict):
        raise ValueError(f"Expected object payload from {path}")
    return loaded


def normalize_record(record: dict) -> dict:
    """Return a normalized record shape for downstream consumers."""
    return {
        "projectId": record.get("projectId", ""),
        "projectNumber": record.get("projectNumber", ""),
        "title": record.get("title", ""),
        "projectTypeName": record.get("projectTypeName", ""),
        "projectTypeId": record.get("projectTypeId", ""),
        "proponentName": record.get("proponentName", ""),
        "assessmentDistricts": record.get("assessmentDistricts", []),
        "sectors": record.get("sectors", []),
        "indigenousGovernments": record.get("indigenousGovernments", []),
        "decisionBodies": record.get("decisionBodies", []),
        "planningCommissions": record.get("planningCommissions", []),
        "projectScope": record.get("projectScope", {}),
        "stage": record.get("stage", {}),
        "stageId": record.get("stageId", ""),
        "stageHistory": record.get("stageHistory", []),
        "outcomes": record.get("outcomes", {}),
        "locations": record.get("locations", []),
    }


def fetch_bucket(start_year: int, end_year: int) -> tuple[list[dict], dict]:
    """Fetch one year bucket and return records plus response metadata."""
    url = build_url(start_year, end_year)
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        raw = resp.read().decode("utf-8")
        headers = dict(resp.headers.items())

    if not raw.strip():
        records: list[dict] = []
    else:
        loaded = json.loads(raw)
        if not isinstance(loaded, list):
            raise ValueError(f"Expected list payload from {url}")
        records = [normalize_record(record) for record in loaded]

    metadata = {
        "url": url,
        "fetched_at": utc_now_iso(),
        "record_count": len(records),
        "sha256": sha256_text(
            json.dumps(records, separators=(",", ":"), sort_keys=True)
        ),
        "content_length": headers.get("Content-Length", ""),
        "content_type": headers.get("Content-Type", ""),
    }
    return records, metadata


def write_bucket(start_year: int, end_year: int, records: list[dict]) -> None:
    """Write one cached year bucket to disk."""
    BUCKET_DIR.mkdir(parents=True, exist_ok=True)
    path = bucket_path(start_year, end_year)
    payload = {
        "startYear": start_year,
        "endYear": end_year,
        "cachedAt": utc_now_iso(),
        "records": records,
    }
    write_zstd_json(path, payload)


def read_bucket(path: Path) -> tuple[dict, list[dict]]:
    """Read one cached bucket file."""
    payload = read_zstd_json(path)
    records = payload.get("records", [])
    return payload, records


def years_from_bucket_path(path: Path) -> tuple[int, int] | None:
    """Parse the canonical year span from a bucket filename."""
    stem = path.stem
    if path.suffix == ".zst":
        stem = Path(stem).stem
    prefix = "projects_"
    if not stem.startswith(prefix):
        return None
    try:
        start_text, end_text = stem[len(prefix) :].split("-", maxsplit=1)
        return int(start_text), int(end_text)
    except ValueError:
        return None


def normalize_bucket_payload_years(
    path: Path, payload: dict, start_year: int, end_year: int
) -> dict:
    """Rewrite a bucket file when its embedded years disagree with its filename."""
    if payload.get("startYear") == start_year and payload.get("endYear") == end_year:
        return payload
    normalized = {
        **payload,
        "startYear": start_year,
        "endYear": end_year,
    }
    write_zstd_json(path, normalized)
    return normalized


def metadata_from_bucket_file(
    path: Path, payload: dict, records: list[dict], prior: dict | None = None
) -> dict:
    """Build state metadata for one on-disk bucket file."""
    start_year = payload["startYear"]
    end_year = payload["endYear"]
    return {
        "startYear": start_year,
        "endYear": end_year,
        "path": str(path),
        "url": build_url(start_year, end_year),
        "fetched_at": (prior or {}).get("fetched_at") or payload.get("cachedAt", ""),
        "record_count": len(records),
        "sha256": sha256_text(
            json.dumps(records, separators=(",", ":"), sort_keys=True)
        ),
        "content_length": str(path.stat().st_size),
        "content_type": "application/zstd",
    }


def sync_state_to_bucket_files(state: dict) -> dict:
    """Rebuild tracked bucket state from bucket files on disk."""
    prior_buckets = state.get("buckets", {})
    synced_buckets: dict[str, dict] = {}

    for path in sorted(BUCKET_DIR.glob("projects_*.json.zst")):
        years = years_from_bucket_path(path)
        if years is None:
            continue
        start_year, end_year = years
        payload, records = read_bucket(path)
        payload = normalize_bucket_payload_years(path, payload, start_year, end_year)
        key = bucket_key(start_year, end_year)
        synced_buckets[key] = metadata_from_bucket_file(
            path, payload, records, prior=prior_buckets.get(key)
        )

    state["buckets"] = synced_buckets
    return state


def resolve_bucket_file(info: dict, key: str) -> Path:
    """Resolve a bucket file path from canonical bucket metadata."""
    start_year = info.get("startYear")
    end_year = info.get("endYear")
    if start_year is None or end_year is None:
        try:
            start_text, end_text = key.split("-", maxsplit=1)
            start_year = int(start_text)
            end_year = int(end_text)
        except ValueError:
            stored_path = info.get("path", "")
            return Path(stored_path) if stored_path else bucket_path(0, 0)
    return bucket_path(start_year, end_year)


def merge_cached_buckets(state: dict) -> dict:
    """Merge all cached buckets into one deduplicated project dataset."""
    merged_by_key: dict[str, dict] = {}
    project_numbers: set[str] = set()
    project_ids: set[str] = set()
    source_buckets: list[str] = []

    for key in sorted(state.get("buckets", {})):
        info = state["buckets"][key]
        path = resolve_bucket_file(info, key)
        info["path"] = str(path)
        if not path.exists():
            continue
        source_buckets.append(key)
        payload, records = read_bucket(path)
        bucket_cached_at = payload.get("cachedAt", "")
        for record in records:
            merge_key = record.get("projectId") or record.get("projectNumber")
            if not merge_key:
                continue
            existing = merged_by_key.get(merge_key)
            wrapped = {
                **record,
                "_cache": {
                    "bucket": key,
                    "bucketCachedAt": bucket_cached_at,
                },
            }
            if (
                existing is None
                or wrapped["_cache"]["bucketCachedAt"]
                > existing["_cache"]["bucketCachedAt"]
            ):
                merged_by_key[merge_key] = wrapped
            if record.get("projectNumber"):
                project_numbers.add(record["projectNumber"])
            if record.get("projectId"):
                project_ids.add(record["projectId"])

    projects = sorted(
        merged_by_key.values(),
        key=lambda item: (
            item.get("projectNumber", ""),
            item.get("projectId", ""),
        ),
    )
    summary = {
        "generatedAt": utc_now_iso(),
        "bucketCount": len(source_buckets),
        "projectCount": len(projects),
        "projectNumberCount": len(project_numbers),
        "projectIdCount": len(project_ids),
        "buckets": source_buckets,
    }
    merged = {"summary": summary, "projects": projects}
    write_zstd_json(MERGED_FILE, merged)
    return summary


def hot_buckets(now_year: int) -> list[tuple[int, int]]:
    """Return the default bucket spec to refresh when no years are provided."""
    return [(now_year, now_year)]


def bucket_specs_from_args(args: argparse.Namespace) -> list[tuple[int, int]]:
    """Resolve the requested bucket specs from CLI arguments."""
    if args.start_year is not None:
        end_year = args.end_year if args.end_year is not None else args.start_year
        if end_year < args.start_year:
            raise SystemExit("end_year must be greater than or equal to start_year")
        if end_year - args.start_year > 5:
            raise SystemExit("YESAB API supports a maximum 5 year span difference")
        return [(args.start_year, end_year)]

    if args.years:
        years = sorted(set(args.years))
        return [(year, year) for year in years]

    return hot_buckets(datetime.now().year)


def refresh_bucket(state: dict, start_year: int, end_year: int, force: bool) -> None:
    """Fetch and store one bucket, or skip it if a cold bucket already exists."""
    key = bucket_key(start_year, end_year)
    path = bucket_path(start_year, end_year)
    exists = path.exists()

    if exists and not force:
        info = state.setdefault("buckets", {}).setdefault(key, {})
        info.update(
            {
                "startYear": start_year,
                "endYear": end_year,
                "path": str(path),
            }
        )
        print(f"Reusing cached {key}: {path}")
        return

    print(f"Fetching bucket {key}...")
    try:
        records, metadata = fetch_bucket(start_year, end_year)
    except urllib.error.URLError as exc:
        raise SystemExit(f"Request failed for bucket {key}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON for bucket {key}: {exc}") from exc

    write_bucket(start_year, end_year, records)
    state.setdefault("buckets", {})[key] = {
        "startYear": start_year,
        "endYear": end_year,
        "path": str(path),
        **metadata,
    }
    print(f"Stored bucket {key} with {metadata['record_count']} records: {path}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--start-year",
        type=int,
        help="Start year for one explicit refresh bucket.",
    )
    parser.add_argument(
        "--end-year",
        type=int,
        help="End year for one explicit refresh bucket. Defaults to --start-year.",
    )
    parser.add_argument(
        "--years",
        type=int,
        nargs="+",
        help="Refresh one or more single-year buckets.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Refetch the requested bucket(s) even if a cache file already exists.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Refresh requested buckets and rebuild the merged cache file."""
    args = parse_args(argv or sys.argv[1:])
    specs = bucket_specs_from_args(args)
    state = load_state()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    BUCKET_DIR.mkdir(parents=True, exist_ok=True)
    sync_state_to_bucket_files(state)

    for start_year, end_year in specs:
        refresh_bucket(state, start_year, end_year, force=args.force)

    sync_state_to_bucket_files(state)
    summary = merge_cached_buckets(state)
    state["merged"] = {
        "path": str(MERGED_FILE),
        **summary,
    }
    save_state(state)
    print(f"State file    : {STATE_FILE}")

    print(
        "Merged cache  :",
        f"{summary['projectCount']} projects",
        f"from {summary['bucketCount']} bucket(s)",
    )
    print(f"Merged dataset: {MERGED_FILE}")

    for key in summary["buckets"]:
        info = state["buckets"].get(key, {})
        print(f"  - {key} : {resolve_bucket_file(info, key)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
