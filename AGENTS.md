# AGENTS.md

`README.md` is the human-facing overview. This file only captures agent-relevant operating constraints and repo conventions.

Preference order for accessing or getting supporting tools:
  uvx > npx > uv tool install > pip install > npm install

## Working Rules

- Preserve the current output split:
  - single-file build at `out/yesab-map-in-one.html`
  - split build under `out/yesab-map/`
- `scripts/build_static_map_split.py` recreates its target directory. Keep it isolated to `out/yesab-map/` or another dedicated directory.
- Update both builders when changing shared behavior such as joins, styling, details panels, or QA generation.
- Keep the low-complexity API bucket cache model unless there is a clear reason to add a more complex sync design.
- You are not the only one working in this directory.
- Use red-green TDD.
- Prefer `uv run` for project commands.

## Measurement Rules

- Work under a task id when possible. Use an issue id, bead id, or short label.
- Use `scripts/run_timed.py -- <command>` for tests, builds, downloads, cache refreshes, packaging, and other material commands.
- Before final response for material work, append one row to `metrics/agent_sessions.jsonl` with `scripts/log_agent_session.py`.
- If the agent UI exposes token counters, record input, cached input, output, and reasoning output tokens.
- If token counters are not available, record `null` by omitting those fields and note that the row is incomplete.
- Record failed tool calls and workaround chains in the session row.
- Record abandoned approaches and durable technical decisions in `metrics/decisions.jsonl`.
- Agent-authored commits should include these trailers:
  - `Task: <id or short label>`
  - `Agent-Session: <session id if available>`
  - `Model: <model>`
  - `Reasoning: <low|medium|high|xhigh>`
  - `Tests: <command/result>`

## Model And Reasoning Guidance

- Start with `gpt-5.5` at `medium` for cross-file implementation, data modeling, architecture, and risky behavior changes.
- Use lower reasoning for mechanical edits, commit-message help, docs-only updates, formatting, and narrow script cleanup.
- Escalate reasoning when failures involve hidden coupling, data correctness, security, concurrency, or cross-module behavior.
- Downgrade once the path is known and remaining work is repetitive.
- Record suspected overkill or underpowered model choices with `--model-fit` and `--model-fit-notes` so future estimates get better.

## Metrics Files

- `metrics/agent_sessions.jsonl`: one row per material agent session or turn.
- `metrics/command_runs.jsonl`: one row per timed command.
- `metrics/decisions.jsonl`: one row per durable decision, abandoned approach, or notable tradeoff.

## End-Of-Task Checklist

- Tests or checks run through `scripts/run_timed.py`, when practical.
- Relevant decisions or abandoned approaches logged.
- Session metrics logged or explicitly noted as unavailable.
- Commit trailers filled when committing.

## API Cache Constraints

- Cache state is shared in `data/api/state.json`.
- `scripts/refresh_api_cache.py` is safe for one writer at a time only. Do not run concurrent refreshes.
- Refresh cache before rebuilding map outputs when working on API-enriched behavior.

## Join Assumptions

- Builders currently match shapefile features to API records by project number.
- Feature properties used for joins:
  - `ProjectID`
  - `Prj_ID`
  - `YESAB_PROJ`
  - `Number`
- API field used for joins:
  - `projectNumber`
- Do not assume complete overlap between shapefile geometry and API project records. Check QA outputs after join changes.

## Generated Artifacts

- Treat `out/`, `data/api/`, `journal/`, and ad hoc probe JSON files as generated or local-working artifacts unless the task says otherwise.
- Do not delete or overwrite unrelated generated artifacts casually.
