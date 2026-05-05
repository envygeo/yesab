from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Iterable


def read_jsonl(path: Path) -> Iterable[dict[str, object]]:
    if not path.exists():
        return []
    rows: list[dict[str, object]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if text:
                rows.append(json.loads(text))
    return rows


def number(value: object) -> int:
    if value is None or value == "":
        return 0
    return int(value)


def summarize(metrics_dir: Path) -> str:
    sessions = list(read_jsonl(metrics_dir / "agent_sessions.jsonl"))
    commands = list(read_jsonl(metrics_dir / "command_runs.jsonl"))
    decisions = list(read_jsonl(metrics_dir / "decisions.jsonl"))

    input_tokens = sum(number(row.get("input_tokens")) for row in sessions)
    cached_tokens = sum(number(row.get("cached_input_tokens")) for row in sessions)
    output_tokens = sum(number(row.get("output_tokens")) for row in sessions)
    reasoning_tokens = sum(number(row.get("reasoning_output_tokens")) for row in sessions)
    command_seconds = sum(float(row.get("elapsed_seconds") or 0) for row in commands)
    failed_commands = sum(1 for row in commands if int(row.get("exit_code") or 0) != 0)
    session_failures = sum(len(row.get("failures") or []) for row in sessions)

    by_model: dict[tuple[str, str], int] = defaultdict(int)
    by_model_fit: dict[str, int] = defaultdict(int)
    for row in sessions:
        by_model[(str(row.get("model") or "unknown"), str(row.get("reasoning") or "unknown"))] += 1
        by_model_fit[str(row.get("model_fit") or "unknown")] += 1

    lines = [
        "Project Metrics Summary",
        f"agent_sessions: {len(sessions)}",
        f"command_runs: {len(commands)}",
        f"decisions: {len(decisions)}",
        f"input_tokens: {input_tokens}",
        f"cached_input_tokens: {cached_tokens}",
        f"output_tokens: {output_tokens}",
        f"reasoning_output_tokens: {reasoning_tokens}",
        f"command_wall_seconds: {round(command_seconds, 3)}",
        f"failed_commands: {failed_commands}",
        f"session_failure_notes: {session_failures}",
        "sessions_by_model:",
    ]
    if by_model:
        for (model, reasoning), count in sorted(by_model.items()):
            lines.append(f"  {model} / {reasoning}: {count}")
    else:
        lines.append("  none")
    lines.append("model_fit:")
    if by_model_fit:
        for model_fit, count in sorted(by_model_fit.items()):
            lines.append(f"  {model_fit}: {count}")
    else:
        lines.append("  none")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize project metrics JSONL files.")
    parser.add_argument("--metrics-dir", type=Path, default=Path("metrics"))
    args = parser.parse_args()
    print(summarize(args.metrics_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
