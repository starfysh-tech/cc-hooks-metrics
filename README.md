# cc-hooks-metrics

SQLite-backed telemetry and analytics for [Claude Code](https://docs.anthropic.com/en/docs/claude-code) hooks. Captures timing, exit codes, and tool-use events per session, and surfaces actionable week-over-week trends in a terminal report.

## What it does

- Wraps any Claude Code hook script to capture wall-clock timing, exit codes, and git context into `hooks.db`
- Logs every Claude tool use (Bash, Edit, Write, Read, etc.) with full input payloads
- Produces an ANSI-colored analytics report with 7 sections:
  - **a) Health** — 24h failure rate and latency summary
  - **b) Failures** — per-step failure rates, exit codes, and timeout proximity
  - **c) Performance** — avg / p95 / max duration per step
  - **d) Usage** — tool distribution, session stats, most-edited files
  - **e) Data quality** — zero-timing rows, duplicate detection
  - **f) Per-project cost** — overhead and failures broken down by repo
  - **g) Week-over-week trends** — visual bar charts comparing last 7d vs prior 7d for failures, coverage gaps, and latency regressions
- Exports OTel-aligned JSON for piping to Claude or a metrics collector

## Requirements

- `bash` 4+
- `sqlite3`
- `awk`, `date`, `/usr/bin/time` (standard on macOS/Linux)

## Installation

```bash
# Copy scripts to your Claude hooks directory
cp *.sh ~/.claude/hooks/
chmod +x ~/.claude/hooks/*.sh

# Wire hooks in ~/.claude/settings.json (see settings-example.json)
```

## Usage

```bash
# Full terminal report
~/.claude/hooks/hooks-report.sh

# OTel-aligned JSON export
~/.claude/hooks/hooks-report.sh --export

# Pipe to Claude for analysis
~/.claude/hooks/hooks-report.sh --export | claude -p \
  "Analyze this hooks telemetry. Identify: 1) errors to fix, \
   2) performance to optimize, 3) coverage gaps to close. Suggest next steps."
```

## Architecture

```
Claude Code event (PostToolUse, etc.)
  │
  ▼
hook-metrics.sh          wraps any hook script, captures timing + git context
  │  /usr/bin/time -p    measures real/user/sys seconds
  │  git rev-parse       captures branch, sha, repo
  │
  ├──► audit-logger.sh   logs tool_input JSON → audit_events table
  └──► mermaid-lint.sh   (example downstream hook)
         │
         ▼
    hooks.db  (SQLite, WAL mode, 30-day rolling window)
         │
         ▼
    hooks-report.sh      read-only analytics over hooks.db
```

## Scripts

| Script | Purpose |
|--------|---------|
| `hook-metrics.sh` | Wrapper: runs any hook with `/usr/bin/time`, inserts into `hook_metrics` |
| `audit-logger.sh` | Reads Claude tool-use JSON from stdin, inserts into `audit_events` |
| `db-init.sh` | Schema definition and shared helpers (`_db_exec`, `_sql_escape`) |
| `hooks-report.sh` | Full analytics report + `--export` JSON mode |
| `mermaid-lint.sh` | Example hook: lints Mermaid diagrams in edited files |
| `migrate-jsonl-to-sqlite.sh` | One-time migration from JSONL log files |
| `migrate-logs-to-sqlite.sh` | Migration from plaintext/JSON log formats |

## Database schema

```sql
-- Tool usage audit trail
audit_events (id, ts, session, tool, input)

-- Hook execution telemetry
hook_metrics (id, ts, hook, step, cmd, exit_code,
              duration_ms, real_s, user_s, sys_s,
              branch, sha, host, repo)
```

## OTel export format

`hooks-report.sh --export` outputs a JSON document using OpenTelemetry naming conventions:

```json
{
  "schema": "claude.hooks.trends/v1",
  "generated_at": "...",
  "summary": {
    "current":  { "claude.hooks.runs": 24428, "claude.hooks.failures": 327, ... },
    "previous": { "claude.hooks.runs": 12166, "claude.hooks.failures": 120, ... }
  },
  "failure_trends": [...],
  "latency_trends": [...],
  "coverage_gaps":  [...]
}
```

Metric names: `claude.hooks.runs`, `claude.hooks.failures`, `claude.hooks.duration.*`
Attributes: `hook.step`, `hook.name`, `vcs.repository`

## License

MIT
