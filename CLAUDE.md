# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the report

```bash
# Traffic-light summary + compact sections (~60-80 lines, default)
~/.claude/hooks/hooks-report.sh

# Full report: traffic lights + compact sections + all 7 legacy detail sections
~/.claude/hooks/hooks-report.sh --verbose

# OTel-aligned JSON export
~/.claude/hooks/hooks-report.sh --export

# Pipe to Claude for analysis
~/.claude/hooks/hooks-report.sh --export | claude -p "Analyze and suggest next steps"
```

Scripts are installed to `~/.claude/hooks/` — the copies in this repo are the source of truth, deployed manually via `cp`.

The database path defaults to `~/.claude/hooks.db` and can be overridden with `CLAUDE_HOOKS_DB`.

## Architecture

**Data flow**: Claude Code event → `hook-metrics.sh` (wrapper) → downstream hook script → `hooks.db`

`hook-metrics.sh` is a **passthrough wrapper** — it takes `EVENT:STEP_NAME` as `$1` and the actual hook script + args as remaining args. It captures wall-clock timing via `/usr/bin/time -p`, git context, and exit code, then inserts a row into `hook_metrics`. The wrapped script's exit code is always preserved.

`audit-logger.sh` reads the Claude tool-use JSON payload from stdin, extracts `tool_name`, `tool_input`, and `session_id`, and inserts into `audit_events`. It echoes stdin through so it can be chained.

`db-init.sh` is sourced by all scripts. It owns the schema and provides:
- `_init_hooks_db` — creates tables or runs `ALTER TABLE` migration (idempotent)
- `_db_exec sql` — write-only helper with `PRAGMA busy_timeout=1000`, stdout suppressed
- `_q sql` — **report-only** read helper using `sqlite3 -separator '|'` with **no** busy_timeout (adding it would emit a result row corrupting pipe reads)
- `_sql_escape` — single-quote doubling via `sed`
- `_maybe_prune_hooks_db` — 1% probabilistic pruning of rows older than 30 days

## hooks-report.sh structure

`assess_and_report()` runs first in all non-export modes. It renders a 2-column traffic-light layout (3 rows: Reliability+Performance / BrokenHooks+Regressions / ReviewGate solo) with a 24h run count + overhead subtitle, followed by an Action Items block for any non-green category. All green → "All clear" message instead. The closing `══════` border is printed by the main dispatch block, not by `assess_and_report()`.

**Default mode** outputs traffic lights + 3 compact sections (~60-80 lines total). **`--verbose`** adds all 7 legacy detail sections after the compact ones. **`--export`** calls `export_json()` instead of any other functions and exits. JSON uses OTel naming: metric names `claude.hooks.*`, attributes `hook.step` / `vcs.repository`.

Three compact section functions called in default + verbose mode (before verbose sections):
- `section_perf_compact()` — Per-step performance table (last 7d), filtered to steps with avg ≥500ms OR configured timeout. Columns: step, runs, avg, max, timeout (bar+% or warning). Capped at 12 rows, sorted by total_ms DESC.
- `section_wow_compact()` — 4-row WoW summary table (Runs/Failures/Fail rate/Overhead), compact failure trend lines (5 REGR + 3 FIXED max), latency regressions (top 3, 15% threshold + total_n≥5), and coverage gaps.
- `section_projects_compact()` — Top 5 repos by overhead (last 7d), with fail rate column only when >0%. Excludes `codex-review` from failure counts.

Seven legacy section functions called from `main` in `--verbose` mode only: `section_health`, `section_failures`, `section_performance`, `section_usage`, `section_quality`, `section_projects`, `section_trends`.

Two summary helpers used by `assess_and_report`:
- `_traffic_light label status [detail]` — one status row (✅/⚠️/❌)
- `_action_item icon badge detail fix` — 2-line action item with `→` fix suggestion

Formatting helper:
- `_fmt_dur ms` — formats duration as "1.5s" if ≥1000ms, else "250ms"

Three visual helpers used only in `section_trends`:
- `_bar val max_val [width]` — proportional `█░` bar, default 30 chars
- `_trend_badge type` — colored `[REGR]`/`[FIXED]`/`[GONE]`/`[NEW]`/`[SLOW]` prefix
- `_pct_change cur prev polarity` — colored `+X.X%` with `lower_better`/`higher_better`/`neutral` polarity

## Key conventions

- `codex-review` uses semantic exit codes (exit 1 = findings, not failure) — exclude it from failure counts with `step NOT IN ('codex-review')` and track it separately
- `_SKIP_HOOKS` regex (`fake-fail|ok-step|echo|test-hook|main`) filters noise from coverage gap detection
- SQLite returns `ROUND(...,0)` as a float string (e.g. `88146.0`) — use `${var%%.*}` before integer comparisons in bash
- All `_q` pipe reads use `while IFS='|' read -r ...` with `${var:-0}` guards; arithmetic comparisons append `2>/dev/null` to suppress non-numeric errors
- Shell-expand `$(whoami)` inside SQL strings for repo path stripping (see `section_projects`)

## Adding a new hook step

1. Create the hook script following the `mermaid-lint.sh` pattern (read JSON from stdin, exit 0 on no-op)
2. Wire it in `~/.claude/settings.json` using `hook-metrics.sh EVENT:STEP_NAME /path/to/script`
3. Add its timeout to `_timeout_for()` in `hooks-report.sh` if it has a configured timeout
