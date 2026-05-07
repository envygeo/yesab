# YESAB Map App Development Cost Report

Prepared: 2026-05-07  
Scope: Repository history through the May 7, 2026 basemap work. This report excludes the cost of preparing this cost report.

## Executive Summary

The YESAB map app was developed at very low direct AI compute cost. The meaningful cost category is human attention: steering agents, reviewing output, deciding tradeoffs, and validating that the result was safe to use.

Estimated total development cost is **CAD $1,100**, with a reasonable range of **CAD $830 to $1,506**.

| Category | Midpoint | Range | Notes |
|---|---:|---:|---|
| AI agent token cost, GPT-5.5-normalized | CAD $151 | CAD $130-$206 | Includes measured Codex token counters plus inferred early unmetered work. |
| Human attention, CAD $100/hr | CAD $950 | CAD $700-$1,300 | Estimated 9.5 hours, range 7-13 hours. |
| **Total** | **CAD $1,101** | **CAD $830-$1,506** | Subscription fees, workstation cost, and salary overhead are not included. |

The project produced a working static map application, API-enriched project details, QA outputs, cache tooling, GeoPackage export, deployment tooling, and documentation. It also moved from a quick prototype into an operationally safer workflow with tests and command/session metrics.

## What The Numbers Mean

Agent time and human attention are different things:

| Time type | Estimate | How to read it |
|---|---:|---|
| Agent active time | 16.1 hours | Time the agents were actively reading, editing, testing, or reasoning, with long idle gaps trimmed. This is not billed as labour. |
| Human attention time | 9.5 hours | Time spent prompting, reviewing, deciding, checking outputs, and supervising risk. This is valued at CAD $100/hr. |
| Calendar duration | Apr 14-May 7, 2026 | The project ran across multiple short work sessions, not continuous full-time work. |

The project cost is therefore best understood as **roughly one to two days of human attention plus a small amount of AI compute**.

## Recommended Budget Treatment

For planning, use **CAD $1,100** as the midpoint internal cost of this app build. For a conservative budget note, say **under CAD $1,600**.

For future similar work, the reliable planning number is not token spend. It is the human review and decision time required to keep generated code correct, auditable, and operationally safe.

## Developer Notes

### Evidence Reviewed

Evidence came from:

- `git log --all --date=iso-strict --shortstat`
- `git log --all --date=iso-strict --numstat`
- `metrics/agent_sessions.jsonl`
- `metrics/command_runs.jsonl`
- `metrics/decisions.jsonl`
- local Codex sessions under `C:\Users\mhwilkie\.codex\sessions`
- local agent/tool directories under `C:\Users\mhwilkie\.claude`, `C:\Users\mhwilkie\.amp`, and `C:\Users\mhwilkie\.kilocode`
- journal reports already committed under `journal/`

The explicit repo metrics workflow starts on May 5, 2026. Earlier work was inferred from commit timing, commit content, local agent artifacts, and the later measured cost profile.

### Pricing Inputs

OpenAI GPT-5.5 standard API pricing was used as requested:

| Token class | USD per 1M tokens | CAD per 1M tokens at 1.3635 |
|---|---:|---:|
| Non-cached input | USD $5.00 | CAD $6.82 |
| Cached input | USD $0.50 | CAD $0.68 |
| Output | USD $30.00 | CAD $40.91 |

Sources:

- OpenAI API pricing page, accessed May 7, 2026: https://openai.com/api/pricing/
- OpenAI GPT-5.5 model page, accessed May 7, 2026: https://developers.openai.com/api/docs/models/gpt-5.5
- Bank of Canada daily exchange rates, May 7, 2026 USD/CAD = 1.3635: https://www.bankofcanada.ca/rates/exchange/daily-exchange-rates/

### Measured Codex Token Cost

Measured Codex sessions in `A:\dev\yesab`, excluding this report-generation session, produced:

| Metric | Value |
|---|---:|
| Sessions with usable token counters | 18 |
| Input tokens | 72,925,552 |
| Cached input tokens | 68,988,160 |
| Non-cached input tokens | 3,937,392 |
| Output tokens | 293,550 |
| Reasoning output tokens, informational | 71,142 |
| GPT-5.5-normalized cost | USD $62.99 |
| GPT-5.5-normalized cost | CAD $85.88 |

Formula:

```text
USD cost =
  ((input_tokens - cached_input_tokens) / 1,000,000 * 5.00)
+ (cached_input_tokens / 1,000,000 * 0.50)
+ (output_tokens / 1,000,000 * 30.00)

CAD cost = USD cost * 1.3635
```

Reasoning output tokens are shown for transparency, but the Codex session totals indicate they are a subset of output accounting rather than an additional token class for this estimate.

### Phase Estimate

| Phase | Evidence | Agent active hours | Human attention hours | Token cost CAD |
|---|---|---:|---:|---:|
| Prototype, API exploration, early map/report build | Apr 14-15 commits; no complete token counters | 7.0 inferred | 4.0 | 65 inferred |
| Cache compression and status refinement | Apr 16-21 commits; Codex session counters | 2.4 measured | 1.0 | 11 |
| API fallback points, GeoPackage export, refactor, metrics | May 5 commits and metrics | 3.7 measured | 2.5 | 47 |
| Deployment and ETL hardening | May 6 commits, tests, metrics | 2.3 measured | 1.5 | 20 |
| Basemap chooser/provider work | May 7 session metrics | 0.6 measured | 0.5 | 8 |
| **Total** | Mixed measured and inferred | **16.1** | **9.5** | **151** |

### Inference Rules

The measured post-April-16 Codex sessions showed high cache reuse, so the missing early AI compute was estimated from observed cost per active hour and per comparable commit cluster, not from uncached worst-case API pricing.

Human attention time was estimated separately from agent activity. The midpoint assumes focused review/steering time around commit clusters, generated artifacts, API decisions, and deployment safety decisions. The range allows for lighter or heavier human review that is not visible in the repo.

Long session idle gaps were trimmed when calculating agent active time. For example, a Codex session file spanning Apr 21 to May 5 was treated as active only around visible event clusters, not as continuous two-week agent work.

### Confidence

The token-cost estimate is medium confidence after May 5 and lower confidence before May 5. The human-attention estimate is necessarily lower precision because it cannot be fully reconstructed from git history.

The conclusion is still stable: even if token cost doubled, it would remain much smaller than the human attention cost.

