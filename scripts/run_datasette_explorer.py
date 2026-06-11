"""Run the local YESAB Datasette explorer with sane defaults.

This is the implementation behind the repository-level ``yesab-explorer``
launcher. It builds missing default explorer artifacts, then starts Datasette
with the standard database, metadata, local plugin directory, cluster-map
runtime dependency, and static bundle mount.

(c)2026 Matt Wilkie, Yukon Government. MIT License.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_datasette_explorer as builder

DEFAULT_PLUGINS_DIR = Path("datasette_plugins")
DEFAULT_STATIC_BUNDLE_NAME = "bundles"


def repo_path(root: Path, path: Path) -> Path:
    """Return an absolute path, resolving relative paths under ``root``."""
    if path.is_absolute():
        return path
    return root / path


@dataclass(frozen=True)
class ExplorerPaths:
    """Resolved paths needed to build and serve the explorer."""

    db_path: Path
    metadata_path: Path
    plugins_dir: Path
    bundle_root: Path | None
    static_bundle_name: str = DEFAULT_STATIC_BUNDLE_NAME

    @classmethod
    def from_args(
        cls,
        root: Path,
        *,
        db_path: Path,
        metadata_path: Path | None,
        plugins_dir: Path,
        bundle_root: Path | None,
        static_bundle_name: str = DEFAULT_STATIC_BUNDLE_NAME,
    ) -> ExplorerPaths:
        resolved_db_path = repo_path(root, db_path)
        return cls(
            db_path=resolved_db_path,
            metadata_path=repo_path(
                root,
                metadata_path or builder.default_metadata_path(resolved_db_path),
            ),
            plugins_dir=repo_path(root, plugins_dir),
            bundle_root=repo_path(root, bundle_root) if bundle_root is not None else None,
            static_bundle_name=static_bundle_name,
        )


def datasette_command(
    paths: ExplorerPaths,
    datasette_args: list[str],
    *,
    include_static_bundles: bool = True,
) -> list[str]:
    """Return the Datasette command for the explorer."""
    command = [
        "uvx",
        "--with",
        "datasette-cluster-map",
        "datasette",
        str(paths.db_path),
        "-m",
        str(paths.metadata_path),
        "--plugins-dir",
        str(paths.plugins_dir),
    ]
    if include_static_bundles and paths.bundle_root is not None:
        command.extend(
            [
                "--static",
                f"{paths.static_bundle_name}:{paths.bundle_root}",
            ]
        )
    command.extend(datasette_args)
    return command


def ensure_outputs(
    paths: ExplorerPaths,
    *,
    rebuild: bool,
    build_missing: bool,
) -> None:
    """Build explorer outputs when requested or when required files are absent."""
    if not rebuild and not build_missing:
        return
    missing_required_output = not paths.db_path.exists() or not paths.metadata_path.exists()
    if not rebuild and not missing_required_output:
        return
    builder.build_explorer(
        paths.db_path,
        metadata_output=paths.metadata_path,
        bundle_root=paths.bundle_root,
    )


def parse_args(argv: list[str] | None = None) -> tuple[argparse.Namespace, list[str]]:
    """Parse wrapper options and return unknown options for Datasette."""
    parser = argparse.ArgumentParser(
        description=(
            "Run the YESAB Datasette explorer. By default this starts the "
            "standard out/yesab-explorer.db with local metadata, plugins, and "
            "bundle static files. Extra options are passed to Datasette."
        )
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=builder.DEFAULT_OUTPUT_PATH,
        help=f"Explorer SQLite database path (default: {builder.DEFAULT_OUTPUT_PATH})",
    )
    parser.add_argument(
        "--metadata",
        type=Path,
        help="Datasette metadata JSON path (default: next to --db)",
    )
    parser.add_argument(
        "--plugins-dir",
        type=Path,
        default=DEFAULT_PLUGINS_DIR,
        help=f"Datasette plugins directory (default: {DEFAULT_PLUGINS_DIR})",
    )
    parser.add_argument(
        "--bundle-root",
        type=Path,
        default=builder.DEFAULT_BUNDLE_ROOT,
        help=f"Project bundle root for /bundles/ static files (default: {builder.DEFAULT_BUNDLE_ROOT})",
    )
    parser.add_argument(
        "--bundle-static-name",
        default=DEFAULT_STATIC_BUNDLE_NAME,
        help="Datasette static mount name for bundle files (default: bundles)",
    )
    parser.add_argument(
        "--no-static-bundles",
        action="store_true",
        help="Do not add Datasette's --static bundles:<path> option.",
    )
    parser.add_argument(
        "--no-build",
        action="store_true",
        help="Start Datasette without building missing explorer outputs first.",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Rebuild the explorer database and metadata before starting Datasette.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the Datasette command instead of running it.",
    )
    args, datasette_args = parser.parse_known_args(argv)
    if datasette_args[:1] == ["--"]:
        datasette_args = datasette_args[1:]
    return args, datasette_args


def main(argv: list[str] | None = None) -> int:
    """Run the Datasette explorer."""
    args, datasette_args = parse_args(argv)
    paths = ExplorerPaths.from_args(
        ROOT,
        db_path=args.db,
        metadata_path=args.metadata,
        plugins_dir=args.plugins_dir,
        bundle_root=args.bundle_root,
        static_bundle_name=args.bundle_static_name,
    )
    command = datasette_command(
        paths,
        datasette_args,
        include_static_bundles=not args.no_static_bundles,
    )
    if args.dry_run:
        print(" ".join(command))
        return 0
    ensure_outputs(
        paths,
        rebuild=args.rebuild,
        build_missing=not args.no_build,
    )
    try:
        return subprocess.run(command).returncode
    except FileNotFoundError as exc:
        print(f"Unable to start Datasette explorer: {exc}", file=sys.stderr)
        return 127
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
