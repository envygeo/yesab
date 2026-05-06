"""Deploy the YESAB map tool repository subset to the ETL workspace.

The deployment mirrors an allowlisted project subset into a dedicated
``yesab_map-toy-maker`` destination. Generated outputs, API caches, metrics,
git metadata, and local working artifacts are intentionally excluded.

(c)2026 Matt Wilkie, Yukon Government. MIT License.
"""

# /// script
# requires-python = ">=3.14"
# ///
from __future__ import annotations

import argparse
import getpass
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DEST = Path(r"\\envgeoserver\dev\YESAB\yesab_map-toy-maker")
MANIFEST_NAME = "deploy_manifest.json"
EXPECTED_DEST_NAME = "yesab_map-toy-maker"

ALLOWLIST = (
    "AGENTS.md",
    "LICENSE",
    "README.md",
    "YESAB_API.md",
    "scripts",
    "tests",
    "yesab_map",
    "data/api/location_overrides.csv",
)

OPTIONAL_ALLOWLIST = (
    "pyproject.toml",
    "uv.lock",
)

EXCLUDED_DIR_NAMES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
}

EXCLUDED_FILE_SUFFIXES = {
    ".pyc",
    ".pyo",
}


def utc_now() -> str:
    """Return a compact UTC timestamp for deploy metadata."""
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def repo_relative(path: Path) -> str:
    """Return a stable slash-separated path relative to the repository root."""
    return path.relative_to(ROOT).as_posix()


def git_status() -> str:
    """Return porcelain status for the source checkout."""
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=ROOT,
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git status failed")
    return result.stdout


def git_commit() -> str:
    """Return the current source commit SHA, or an empty string outside git."""
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def should_include(path: Path) -> bool:
    """Return whether a filesystem item should be included in the deploy set."""
    if any(part in EXCLUDED_DIR_NAMES for part in path.parts):
        return False
    if path.is_file() and path.suffix in EXCLUDED_FILE_SUFFIXES:
        return False
    return True


def iter_deploy_files() -> list[Path]:
    """Return all source files included in the deployment."""
    roots = [*ALLOWLIST, *OPTIONAL_ALLOWLIST]
    files: list[Path] = []
    for item in roots:
        path = ROOT / item
        if not path.exists():
            continue
        if path.is_file():
            if should_include(path):
                files.append(path)
            continue
        for child in path.rglob("*"):
            if child.is_file() and should_include(child):
                files.append(child)
    return sorted(files, key=repo_relative)


def safe_destination(dest: Path, allow_any_dest: bool) -> None:
    """Refuse to mirror into a destination that does not look dedicated."""
    resolved_name = dest.name.rstrip("\\/")
    if allow_any_dest or resolved_name == EXPECTED_DEST_NAME:
        return
    raise ValueError(
        f"destination must be named {EXPECTED_DEST_NAME!r}; "
        "pass --allow-any-dest to override"
    )


def run_tests(task_id: str) -> int:
    """Run the repo unit tests through the timed-command wrapper."""
    result = subprocess.run(
        [
            "uv",
            "run",
            "scripts/run_timed.py",
            "--task-id",
            task_id,
            "--label",
            "deploy-preflight-tests",
            "--",
            "uv",
            "run",
            "python",
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests",
        ],
        cwd=ROOT,
        check=False,
    )
    return result.returncode


def copy_to_stage(files: list[Path], stage_dir: Path) -> None:
    """Copy deploy files into the temporary staging directory."""
    for source in files:
        relative = source.relative_to(ROOT)
        target = stage_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def mirror_with_python(stage_dir: Path, dest: Path) -> None:
    """Mirror staged files to destination using Python stdlib copy/delete."""
    dest.mkdir(parents=True, exist_ok=True)
    stage_files = {
        path.relative_to(stage_dir)
        for path in stage_dir.rglob("*")
        if path.is_file()
    }
    for path in sorted(dest.rglob("*"), reverse=True):
        relative = path.relative_to(dest)
        if path.is_file() and relative not in stage_files:
            path.unlink()
        elif path.is_dir():
            try:
                path.rmdir()
            except OSError:
                pass
    for relative in sorted(stage_files):
        source = stage_dir / relative
        target = dest / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def mirror_with_robocopy(stage_dir: Path, dest: Path) -> None:
    """Mirror staged files to destination with robocopy."""
    dest.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            "robocopy",
            str(stage_dir),
            str(dest),
            "/MIR",
            "/NFL",
            "/NDL",
            "/NJH",
            "/NJS",
            "/NP",
        ],
        check=False,
    )
    if result.returncode >= 8:
        raise RuntimeError(f"robocopy failed with exit code {result.returncode}")


def mirror_stage(stage_dir: Path, dest: Path, copy_engine: str) -> str:
    """Mirror staged files to destination and return the copy engine used."""
    if copy_engine == "python":
        mirror_with_python(stage_dir, dest)
        return "python"
    if copy_engine == "robocopy":
        mirror_with_robocopy(stage_dir, dest)
        return "robocopy"
    if os.name == "nt" and shutil.which("robocopy"):
        mirror_with_robocopy(stage_dir, dest)
        return "robocopy"
    mirror_with_python(stage_dir, dest)
    return "python"


def write_manifest(
    dest: Path,
    files: list[Path],
    source_commit: str,
    copy_engine: str,
    dry_run: bool,
    tests_run: bool,
) -> None:
    """Write deployment metadata into the destination."""
    manifest = {
        "deployed_at_utc": utc_now(),
        "source_root": str(ROOT),
        "destination": str(dest),
        "source_commit": source_commit,
        "deployed_by": getpass.getuser(),
        "copy_engine": copy_engine,
        "dry_run": dry_run,
        "tests_run": tests_run,
        "copied_file_count": len(files),
        "copied_paths": [repo_relative(path) for path in files],
        "etl_command": (
            'uv run "$(FME_MF_DIR)/yesab_map-toy-maker/scripts/'
            'refresh_and_build_geopackage.py" '
            '"$(FME_MF_DIR)/yesab_map-toy-output/yesab-projects.gpkg"'
        ),
    }
    dest.mkdir(parents=True, exist_ok=True)
    (dest / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def smoke_check(dest: Path) -> int:
    """Run a lightweight import-free help check from the destination copy."""
    result = subprocess.run(
        [
            "uv",
            "run",
            str(dest / "scripts" / "refresh_and_build_geopackage.py"),
            "--help",
        ],
        check=False,
    )
    return result.returncode


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse deploy command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dest",
        type=Path,
        default=DEFAULT_DEST,
        help=f"Destination directory (default: {DEFAULT_DEST})",
    )
    parser.add_argument(
        "--task-id",
        default="deploy-production",
        help="Task id used for timed preflight commands.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the deployment plan without copying files.",
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Allow deploy from a checkout with uncommitted changes.",
    )
    parser.add_argument(
        "--skip-tests",
        action="store_true",
        help="Skip preflight unit tests.",
    )
    parser.add_argument(
        "--skip-smoke",
        action="store_true",
        help="Skip the post-copy --help smoke check.",
    )
    parser.add_argument(
        "--allow-any-dest",
        action="store_true",
        help="Allow mirroring to a directory not named yesab_map-toy-maker.",
    )
    parser.add_argument(
        "--copy-engine",
        choices=("auto", "robocopy", "python"),
        default="auto",
        help="Copy implementation to use. auto prefers robocopy on Windows.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Deploy the allowlisted project subset to production."""
    args = parse_args(sys.argv[1:] if argv is None else argv)
    dest = args.dest
    try:
        safe_destination(dest, args.allow_any_dest)
        status = git_status()
    except (RuntimeError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2

    if status and not args.allow_dirty:
        print(
            "ERROR: source checkout has uncommitted changes; "
            "commit/stash them or pass --allow-dirty",
            file=sys.stderr,
        )
        print(status, file=sys.stderr)
        return 2

    files = iter_deploy_files()
    source_commit = git_commit()
    tests_run = not args.skip_tests and not args.dry_run

    print(f"Source: {ROOT}")
    print(f"Destination: {dest}")
    print(f"Source commit: {source_commit or '(unknown)'}")
    print(f"Files selected: {len(files)}")

    if args.dry_run:
        print("Dry run: no files copied.")
        for path in files:
            print(f"  {repo_relative(path)}")
        return 0

    if tests_run:
        test_exit = run_tests(args.task_id)
        if test_exit:
            print(f"ERROR: preflight tests failed with exit code {test_exit}", file=sys.stderr)
            return test_exit

    with tempfile.TemporaryDirectory(prefix="yesab-deploy-") as tmp:
        stage_dir = Path(tmp) / "stage"
        copy_to_stage(files, stage_dir)
        copy_engine = mirror_stage(stage_dir, dest, args.copy_engine)

    write_manifest(dest, files, source_commit, copy_engine, False, tests_run)
    print(f"Copied {len(files)} files using {copy_engine}.")
    print(f"Wrote {dest / MANIFEST_NAME}")

    if not args.skip_smoke:
        smoke_exit = smoke_check(dest)
        if smoke_exit:
            print(f"ERROR: smoke check failed with exit code {smoke_exit}", file=sys.stderr)
            return smoke_exit

    print(
        'ETL command: uv run "$(FME_MF_DIR)/yesab_map-toy-maker/scripts/'
        'refresh_and_build_geopackage.py" '
        '"$(FME_MF_DIR)/yesab_map-toy-output/yesab-projects.gpkg"'
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
