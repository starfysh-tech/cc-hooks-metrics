# cc-hooks-metrics

Fast, actionable overview of Claude Code hook health — see what's broken, slow, or regressing without wading through raw data. Designed to be shareable.

## What it does

Claude Code hooks run scripts on events (tool use, file edits, session start, etc.). This tool collects timing, exit codes, and git context from every hook execution, then surfaces the information that matters:

- **Traffic-light status** across 5 categories: reliability, performance, broken hooks, regressions, review gate
- **Actionable items** grouped by step — what's wrong and what to do about it
- **Trend detection** — failure regressions, latency regressions, and fixes week-over-week
- **OTel-aligned JSON export** for piping to Claude or other tools

## Usage

```bash
# Interactive TUI (default in a terminal)
# Dashboard: lights + grouped actions. Press 'd' for detail (perf, WoW, projects).
hooks-report.sh

# Static output — lean: lights, grouped actions, REGR/SLOW trends (~25-40 lines)
hooks-report.sh --static
hooks-report.sh | cat

# Verbose: adds perf table, WoW summary, top projects, FIXED/GONE trends + 7 legacy sections
hooks-report.sh --verbose

# JSON export
hooks-report.sh --export

# Pipe to Claude for analysis
hooks-report.sh --export | claude -p "Analyze and suggest next steps"
```

Mode is auto-detected: TTY → Textual TUI; non-TTY or `--static` → Rich static text; `--export` → JSON.

## How it works

```
Claude Code event
  → hook-metrics.sh (passthrough wrapper — captures timing, git context, exit code)
    → your hook script (mermaid-lint, pytest, eslint, etc.)
      → hooks.db (SQLite, WAL mode, 30-day rolling window)
        → hooks-report.sh (Python TUI / static / JSON)
```

- **hook-metrics.sh** wraps any hook script, records wall-clock time via `/usr/bin/time -p`, and inserts a row into `hook_metrics`. The wrapped script's exit code is always preserved.
- **audit-logger.sh** captures tool-use JSON payloads into `audit_events` (stdin passthrough, can be chained).
- **hooks-report.sh** reads the SQLite database and renders the report.

## Setup

### Prerequisites

- Python 3.10+
- `pip install 'textual>=8.0,<9.0' 'rich>=14.0'`
- `bash`, `sqlite3`, `awk`, `/usr/bin/time`

### Install

```bash
git clone <repo-url> cc-hooks-metrics
cd cc-hooks-metrics
rsync -a --delete hooks_report/ ~/.claude/hooks/hooks_report/
install -m 755 hooks-report.sh hook-metrics.sh audit-logger.sh db-init.sh \
  ~/.claude/hooks/
```

### Wire hooks in Claude Code

Add to `~/.claude/settings.json` (see `settings-example.json` for full examples):

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": { "tool_name": "Write" },
        "hooks": [
          "~/.claude/hooks/hook-metrics.sh PostToolUse:mermaid-lint ~/.claude/hooks/mermaid-lint.sh"
        ]
      }
    ]
  }
}
```

### Database

Defaults to `~/.claude/hooks.db`. Override with `CLAUDE_HOOKS_DB` env var.

## Project structure

```
hooks-report.sh          # 2-line Python wrapper (entry point)
hooks-report-legacy.sh   # Original bash (rollback reference)
hook-metrics.sh          # Passthrough timing wrapper (bash, unchanged)
audit-logger.sh          # Tool-use JSON logger (bash, unchanged)
db-init.sh               # Schema + SQLite helpers (bash, unchanged)

hooks_report/            # Python reporting package (Textual + Rich)
  __main__.py            # Entry point: mode dispatch
  cli.py                 # argparse: --export, --verbose, --static, --db
  config.py              # Timeouts, thresholds, constants
  db.py                  # HooksDB: typed dataclasses + SQLite queries
  render.py              # Rich rendering helpers
  static.py              # Static/piped output assembly
  tui.py                 # Textual TUI: dashboard + detail screen
```

## Database schema

```sql
-- Hook execution telemetry
hook_metrics (id, ts, hook, step, cmd, exit_code,
              duration_ms, real_s, user_s, sys_s,
              branch, sha, host, repo)

-- Tool usage audit trail
audit_events (id, ts, session, tool, input)
```

## OTel export format

`hooks-report.sh --export` outputs JSON using OpenTelemetry naming conventions:

```json
{
  "schema": "claude.hooks.trends/v1",
  "generated_at": "...",
  "summary": {
    "current":  { "claude.hooks.runs": 26629, "claude.hooks.failures": 281, ... },
    "previous": { "claude.hooks.runs": 12166, "claude.hooks.failures": 107, ... }
  },
  "failure_trends": [...],
  "latency_trends": [...],
  "coverage_gaps":  [...]
}
```

Metric names: `claude.hooks.runs`, `claude.hooks.failures`, `claude.hooks.duration.*`
Attributes: `hook.step`, `vcs.repository`

## License

MIT
