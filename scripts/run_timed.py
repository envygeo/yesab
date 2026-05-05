from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_LOG = Path("metrics") / "command_runs.jsonl"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def append_jsonl(path: Path, row: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a command, record elapsed wall time, and return the command exit code."
    )
    parser.add_argument("--task-id", default="", help="Task, issue, or short work label.")
    parser.add_argument("--label", default="", help="Human-readable command label.")
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG, help="JSONL metrics file.")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="Command after --.")
    args = parser.parse_args(argv)
    if args.command and args.command[0] == "--":
        args.command = args.command[1:]
    if not args.command:
        parser.error("provide a command after --")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    start = utc_now()
    start_monotonic = time.perf_counter()
    result = subprocess.run(args.command, shell=False)
    end_monotonic = time.perf_counter()
    end = utc_now()

    row = {
        "task_id": args.task_id,
        "label": args.label,
        "command": args.command,
        "cwd": os.getcwd(),
        "started_at": start,
        "ended_at": end,
        "elapsed_seconds": round(end_monotonic - start_monotonic, 3),
        "exit_code": result.returncode,
    }
    append_jsonl(args.log, row)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
