# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Purpose

`cc-hooks-metrics` gives Claude Code users a **fast, actionable overview of hook health** — so you can immediately see what's broken, slow, or regressing without wading through raw data. Designed to be shareable, not just personal.

## Working in this repo

When you identify enhancements, improvements, or refactor opportunities that are **outside the scope of the current task**, add them to `TODO.md` (Parking Lot section) and continue with the current work. Do not implement them without explicit approval. This keeps changes focused and lets Randall review potential work before it's prioritized.

## Running the report

```bash
# Interactive TUI (default when running in a terminal)
# Dashboard: traffic lights + grouped action items. Press 'd' for detail (perf, WoW, projects).
~/.claude/hooks/hooks-report.sh

# Static output — lean: lights, grouped actions, REGR/SLOW trend lines (~25-40 lines)
~/.claude/hooks/hooks-report.sh --static
~/.claude/hooks/hooks-report.sh | cat

# Verbose: adds perf table, WoW summary, top projects, FIXED/GONE trends, + 7 legacy sections
~/.claude/hooks/hooks-report.sh --verbose

# OTel-aligned JSON export
~/.claude/hooks/hooks-report.sh --export

# Show recent sessions
~/.claude/hooks/hooks-report.sh --sessions

# Drill into a specific step
~/.claude/hooks/hooks-report.sh --step audit-logger

# Pipe to Claude for analysis
~/.claude/hooks/hooks-report.sh --export | claude -p "Analyze and suggest next steps"
```

Mode is auto-detected: TTY → Textual TUI; non-TTY or `--static` → Rich static output; `--export` → JSON.

## Deployment

Scripts are installed to `~/.claude/hooks/` — the copies in this repo are the source of truth. See `TODO.md` for planned distribution improvements (Homebrew, install script).

```bash
# Deploy Python package + wrapper
rsync -a --delete hooks_report/ ~/.claude/hooks/hooks_report/
install -m 755 hooks-report.sh ~/.claude/hooks/hooks-report.sh
```

The database path defaults to `~/.claude/hooks.db` and can be overridden with `CLAUDE_HOOKS_DB`.

## Architecture

**Data flow**: Claude Code event → `hook-metrics.sh` (wrapper) → downstream hook script → `hooks.db`

### Data ingestion scripts (unchanged bash)

`hook-metrics.sh` is a **passthrough wrapper** — takes `EVENT:STEP_NAME` as `$1` and the actual hook script + args as remaining args. Captures wall-clock timing via `/usr/bin/time -p`, git context, and exit code, then inserts a row into `hook_metrics`. The wrapped script's exit code is always preserved.

`audit-logger.sh` reads the Claude tool-use JSON payload from stdin, extracts `tool_name`, `tool_input`, and `session_id`, and inserts into `audit_events`. Echoes stdin through so it can be chained.

`db-init.sh` is sourced by all scripts. Owns the schema and provides:
- `_init_hooks_db` — creates tables or runs `ALTER TABLE` migration (idempotent)
- `_db_exec sql` — write-only helper with `PRAGMA busy_timeout=1000`, stdout suppressed
- `_q sql` — **report-only** read helper using `sqlite3 -separator '|'` with **no** busy_timeout (adding it would emit a result row corrupting pipe reads)
- `_sql_escape` — single-quote doubling via `sed`
- `_maybe_prune_hooks_db` — 1% probabilistic pruning of rows older than 30 days

### Python package: hooks_report/

Rewrite of the original 1331-line bash report in Python (Textual 8.0.0 + Rich 14.3.3).

```
hooks_report/
  __init__.py       # empty
  __main__.py       # entry: export/static/tui dispatch, lazy Textual import
  cli.py            # argparse: --export, --export-spans, --verbose, --static, --db, --include-sensitive
  config.py         # STEP_TIMEOUTS, SEMANTIC_EXIT_STEPS, thresholds, SKIP_HOOKS_PATTERN, OTLP constants
  db.py             # HooksDB: typed dataclasses + SQLite queries
  otlp.py           # OTLP/HTTP JSON export: build_otlp_payload(), send_spans(); zero external deps
  render.py         # Rich helpers: fmt_dur, bar_chart, trend_badge, pct_change, traffic_light_grid
  spans.py          # OTel span model: Span dataclass, hook_metric_to_span, audit_event_to_span, spans_to_dict
  static.py         # Rich Console output: compact sections + verbose sections + export_json
  tui.py            # Textual app: HooksReportApp (dashboard) + DetailScreen + SessionsScreen + StepDrillScreen
```

**hooks-report.sh** is a Python wrapper with venv detection:
```bash
#!/usr/bin/env bash
DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="${DIR}/.venv/bin/python3"
[ -x "$PYTHON" ] || PYTHON=python3
PYTHONPATH="$DIR" exec "$PYTHON" -m hooks_report "$@"
```

**hooks-report-legacy.sh** — original bash (rollback reference).

### Output structure

**Default / --static mode** (~25-40 lines):
1. Traffic-light grid (Reliability / Performance / Broken Hooks / Regressions / Review Gate)
2. Action items grouped by step — one entry per step with all issues deduplicated (or "All clear")
3. `section_wow_compact()` — REGR/SLOW trend lines only (FIXED/GONE suppressed in default)

**TUI mode** (default when TTY): Dashboard with traffic lights + grouped action items.
Keybindings: `d` → detail (perf, WoW, projects), `s` → sessions, `t` → step reliability.
All data accessible interactively without flags.

**--verbose mode** adds compact sections + 7 legacy detail sections:
- `section_perf_compact()` — per-step performance table (last 7d, avg ≥500ms or has timeout, max 12 rows)
- `section_wow_compact()` — full WoW summary + REGR/FIXED/SLOW/GONE trend lines
- `section_projects_compact()` — top 5 repos by overhead
- 7 legacy detail sections

**--export mode** — OTel-aligned JSON, schema `claude.hooks.trends/v1`, metric names `claude.hooks.*`, attributes `hook.step` / `vcs.repository`.

**--export-spans mode** — OTel span JSON, schema `claude.hooks.spans/v1`. One span per hook_metrics row (`hook.{step}`, kind=3 CLIENT) and per audit_events row (`tool.{tool_name}`, kind=1 INTERNAL). Redacts sensitive fields by default; `--include-sensitive` disables redaction. Skip warnings on corrupt rows go to stderr. If `HOOKS_METRICS_OTLP_ENDPOINT` is set, also POSTs spans to the OTLP endpoint before printing JSON (`otlp.py`); `HOOKS_METRICS_OTLP_HEADERS` sets auth headers (`key=value,key2=value2`).

## Key conventions

- `codex-review` uses semantic exit codes (exit 1 = findings, not failure) — excluded from failure counts via `step NOT IN ('codex-review')`, tracked separately; `SEMANTIC_EXIT_STEPS` set in `config.py` controls this for span export too
- OTel SpanKind: hooks → `kind=3` (CLIENT — spawn external processes); tools (audit events) → `kind=1` (INTERNAL — Claude-internal operations)
- `SKIP_HOOKS` regex (`fake-fail|ok-step|echo|test-hook|main`) filters noise from coverage gap detection — use `re.fullmatch()` not `re.search()`
- `ROUND(...,0)` in SQLite returns float — use `int(round(float(val)))` not `int(val)`
- NULL failure_rate → `None` in Python, `null` in JSON (not `0`)
- `CLAUDE_HOOKS_DB` env var overrides DB path
- Empty/missing DB: auto-init schema on first connect, returns zero rows (no crash)
- Textual 8.x: do **not** override `ScrollableContainer` CSS — it already has `height: 1fr; overflow: auto auto` in DEFAULT_CSS; overriding breaks the layout
- All Rich content in Textual widgets must be `rich.text.Text` objects, not markup strings

## Adding a new hook step

1. Create the hook script following the `mermaid-lint.sh` pattern (read JSON from stdin, exit 0 on no-op)
2. Wire it in `~/.claude/settings.json` using `hook-metrics.sh EVENT:STEP_NAME /path/to/script`
3. Add its timeout to `config.STEP_TIMEOUTS` in `hooks_report/config.py` if it has a configured timeout

## Guardrails

Optional guardrail scripts live in `guardrails/`. All use plain `python3` (stdlib only) for portability.

| Script | Event | Purpose |
|--------|-------|---------|
| `guard-security.py` | PreToolUse | Blocks destructive Bash + `.env` access |
| `guard-python-lint.py` | PostToolUse | Runs `ruff check` on `.py` Write/Edit |
| `guard-python-typecheck.py` | PostToolUse | Runs `ty check` on `.py` Write/Edit |
| `guard-auto-allow.py` | PermissionRequest | Auto-allows read-only tools |

All guardrails exit 2 + stderr to block (Claude self-corrects), exit 0 to allow. Wired via `hook-metrics.sh` for tracking. See `settings-guardrails-example.json` for copy-paste wiring.

`GUARDRAIL_STEPS` in `config.py` controls reporting queries. `event-log` step is already in `SKIP_HOOKS_PATTERN`.

### Hook Protocol

- **PreToolUse**: stdin `{tool_name, tool_input}`. Exit 2 + stderr = block.
- **PostToolUse**: stdin `{tool_name, tool_input, tool_use_id}`. Exit 2 + stderr = block.
- **PermissionRequest**: stdout JSON `{hookSpecificOutput: {hookEventName, decision: {behavior: "allow"}}}`. No output = defer to user.

### Naming convention

Guardrail steps use `guard-` prefix (e.g., `guard-security`, `guard-python-lint`).
