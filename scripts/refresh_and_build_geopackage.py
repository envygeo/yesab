"""Refresh YESAB source data and build an enriched GeoPackage.

This is the one-command path for GIS users who want the freshest local
GeoPackage and do not need to build the static map outputs.

(c)2026 Matt Wilkie, Yukon Government. MIT License.
"""

# /// script
# requires-python = ">=3.14"
# ///
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_geopackage
from scripts import download_project_map_archive
from scripts import refresh_api_cache


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse wrapper arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "output",
        nargs="?",
        type=Path,
        default=build_geopackage.DEFAULT_OUTPUT_PATH,
        help=f"GeoPackage output path (default: {build_geopackage.DEFAULT_OUTPUT_PATH})",
    )
    parser.add_argument(
        "--start-year",
        type=int,
        help="Start year for one explicit API refresh bucket.",
    )
    parser.add_argument(
        "--end-year",
        type=int,
        help="End year for one explicit API refresh bucket. Defaults to --start-year.",
    )
    parser.add_argument(
        "--years",
        type=int,
        nargs="+",
        help="Refresh one or more single-year API buckets.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Refetch the requested API bucket(s) even if cache files already exist.",
    )
    return parser.parse_args(argv)


def cache_args_from(args: argparse.Namespace) -> list[str]:
    """Return arguments to forward to refresh_api_cache."""
    forwarded: list[str] = []
    if args.start_year is not None:
        forwarded.extend(["--start-year", str(args.start_year)])
    if args.end_year is not None:
        forwarded.extend(["--end-year", str(args.end_year)])
    if args.years:
        forwarded.append("--years")
        forwarded.extend(str(year) for year in args.years)
    if args.force:
        forwarded.append("--force")
    return forwarded


def main(argv: list[str] | None = None) -> int:
    """Download shapefiles, refresh the API cache, and write the GeoPackage."""
    args = parse_args(sys.argv[1:] if argv is None else argv)

    print("Step 1/3: refresh YESAB project map archive")
    download_project_map_archive.main()

    print("Step 2/3: refresh YESAB API cache")
    cache_exit = refresh_api_cache.main(cache_args_from(args))
    if cache_exit:
        return cache_exit

    print("Step 3/3: build GeoPackage")
    counts = build_geopackage.write_geopackage(args.output)
    print(f"Wrote {args.output}")
    for layer_name, count in counts.items():
        print(f"  {layer_name}: {count} features")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
