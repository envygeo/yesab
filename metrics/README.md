# Metrics

This directory stores lightweight project telemetry as JSON Lines.

## Files

- `agent_sessions.jsonl`: one row per material agent session or turn.
- `command_runs.jsonl`: one row per timed command.
- `decisions.jsonl`: one row per durable decision, abandoned approach, or notable tradeoff.

## Principles

- Prefer sparse, durable facts over long prose.
- Record what was measured separately from what was inferred.
- Keep sensitive content out of metrics. Use task ids and summaries, not full prompts.
- Do not rewrite history casually. Append correction rows if needed.

## Recommended Cadence

- Log commands as they run through `scripts/run_timed.py`.
- Log agent sessions before final response or before context is lost.
- Log decisions when an approach is selected, rejected, or backed out.
