from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_LOG = Path("metrics") / "agent_sessions.jsonl"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def optional_int(value: str | None) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def append_jsonl(path: Path, row: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Append one material agent-session metrics row.")
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG, help="JSONL metrics file.")
    parser.add_argument("--task-id", default="", help="Task, issue, or short work label.")
    parser.add_argument("--session-id", default="", help="Agent or UI session id, if known.")
    parser.add_argument("--model", default="", help="Model used, for example gpt-5.5.")
    parser.add_argument("--reasoning", default="", help="Reasoning effort: low, medium, high, or xhigh.")
    parser.add_argument(
        "--model-fit",
        choices=("appropriate", "overshot", "undershot", "unknown"),
        default="unknown",
        help="Whether the chosen model/reasoning looked right in hindsight.",
    )
    parser.add_argument("--model-fit-notes", default="", help="Short hindsight note on model choice.")
    parser.add_argument("--started-at", default="", help="UTC ISO timestamp, if known.")
    parser.add_argument("--ended-at", default="", help="UTC ISO timestamp. Defaults to now.")
    parser.add_argument("--input-tokens", type=optional_int, default=None)
    parser.add_argument("--cached-input-tokens", type=optional_int, default=None)
    parser.add_argument("--output-tokens", type=optional_int, default=None)
    parser.add_argument("--reasoning-output-tokens", type=optional_int, default=None)
    parser.add_argument("--commit", action="append", default=[], help="Commit id produced by this session.")
    parser.add_argument("--test", action="append", default=[], help="Test/check run and result.")
    parser.add_argument("--failure", action="append", default=[], help="Failed tool call or workaround note.")
    parser.add_argument("--notes", default="", help="Short summary, including inferred/unknown fields.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_tokens = args.input_tokens
    cached_input_tokens = args.cached_input_tokens
    non_cached_input_tokens = None
    if input_tokens is not None and cached_input_tokens is not None:
        non_cached_input_tokens = input_tokens - cached_input_tokens

    row = {
        "task_id": args.task_id,
        "session_id": args.session_id,
        "model": args.model,
        "reasoning": args.reasoning,
        "model_fit": args.model_fit,
        "model_fit_notes": args.model_fit_notes,
        "started_at": args.started_at,
        "ended_at": args.ended_at or utc_now(),
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_input_tokens,
        "non_cached_input_tokens": non_cached_input_tokens,
        "output_tokens": args.output_tokens,
        "reasoning_output_tokens": args.reasoning_output_tokens,
        "commits": args.commit,
        "tests": args.test,
        "failures": args.failure,
        "notes": args.notes,
    }
    append_jsonl(args.log, row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
