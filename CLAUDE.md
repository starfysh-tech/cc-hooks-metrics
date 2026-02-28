# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the report

```bash
# Visual ANSI report (sections a–g)
~/.claude/hooks/hooks-report.sh

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

Seven section functions called from `main`: `section_health`, `section_failures`, `section_performance`, `section_usage`, `section_quality`, `section_projects`, `section_trends`.

Three visual helpers used only in `section_trends`:
- `_bar val max_val [width]` — proportional `█░` bar, default 30 chars
- `_trend_badge type` — colored `[REGR]`/`[FIXED]`/`[GONE]`/`[NEW]`/`[SLOW]` prefix
- `_pct_change cur prev polarity` — colored `+X.X%` with `lower_better`/`higher_better`/`neutral` polarity

`--export` mode calls `export_json()` instead of the section functions and exits. JSON uses OTel naming: metric names `claude.hooks.*`, attributes `hook.step` / `vcs.repository`.

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
