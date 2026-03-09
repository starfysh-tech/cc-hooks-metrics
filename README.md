# cc-hooks-metrics

Fast, actionable overview of Claude Code hook health — see what's broken, slow, or regressing without wading through raw data. Designed to be shareable.

## What it does

Claude Code hooks run scripts on events (tool use, file edits, session start, etc.). This tool collects timing, exit codes, and git context from every hook execution, then surfaces the information that matters:

- **Traffic-light status** across 5 categories: reliability, performance, broken hooks, regressions, review gate
- **Actionable items** grouped by step — what's wrong and what to do about it
- **Trend detection** — failure regressions, latency regressions, and fixes week-over-week
- **OTel span export** — one span per hook execution and tool use, with optional OTLP backend push
- **Interactive TUI** — dashboard + detail screens for perf, sessions, step reliability, and tuning advisor

## Usage

```bash
# Interactive TUI (default in a terminal)
# Dashboard: lights + grouped actions. Press 'd' for detail, 's' for sessions, 't' for step reliability, 'a' for advisor.
hooks-report.sh

# Static output — lean: lights, grouped actions, REGR/SLOW trends (~25-40 lines)
hooks-report.sh --static
hooks-report.sh | cat

# Verbose: adds perf table, WoW summary, top projects, FIXED/GONE trends + 7 legacy sections
hooks-report.sh --verbose

# OTel trends JSON export
hooks-report.sh --export

# OTel span JSON export (one span per hook execution / tool use)
hooks-report.sh --export-spans
hooks-report.sh --export-spans --include-sensitive  # disable field redaction

# Show recent sessions
hooks-report.sh --sessions

# Drill into a specific step
hooks-report.sh --step audit-logger

# Pipe to Claude for analysis
hooks-report.sh --export | claude -p "Analyze and suggest next steps"
```

Mode is auto-detected: TTY → Textual TUI; non-TTY or `--static` → Rich static text; `--export`/`--export-spans` → JSON.

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

### Install

```bash
git clone <repo-url> cc-hooks-metrics
cd cc-hooks-metrics
./install.sh   # checks deps, deploys to ~/.claude/hooks/, patches settings.json
```

Or manually:

```bash
pip install 'textual>=8.0,<9.0' 'rich>=14.0'
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

### Guardrails (optional)

Optional guardrail scripts in `guardrails/` block unsafe operations and auto-allow safe ones:

| Script | Event | Purpose |
|--------|-------|---------|
| `guard-security.py` | PreToolUse | Blocks destructive Bash + `.env` access |
| `guard-python-lint.py` | PostToolUse | Runs `ruff check` on `.py` Write/Edit |
| `guard-python-typecheck.py` | PostToolUse | Runs `ty check` on `.py` Write/Edit |
| `guard-ts-typecheck.py` | PostToolUse | Runs `tsc --noEmit` on `.ts`/`.tsx` Write/Edit |
| `guard-auto-allow.py` | PermissionRequest | Auto-allows read-only tools |

See `settings-guardrails-example.json` for copy-paste wiring.

### Database

Defaults to `~/.claude/hooks.db`. Override with `CLAUDE_HOOKS_DB` env var.

## Project structure

```
hooks-report.sh          # 2-line Python wrapper (entry point)
hook-metrics.sh          # Passthrough timing wrapper (bash, unchanged)
audit-logger.sh          # Tool-use JSON logger (bash, unchanged)
db-init.sh               # Schema + SQLite helpers (bash, unchanged)
install.sh               # Install script: deps, deploy, settings patch

hooks_report/            # Python reporting package (Textual + Rich)
  __main__.py            # Entry point: mode dispatch
  cli.py                 # argparse: --export, --export-spans, --verbose, --static, --sessions, --step, --db, --include-sensitive
  config.py              # Timeouts, thresholds, SKIP_HOOKS_PATTERN, OTLP constants
  db.py                  # HooksDB: typed dataclasses + SQLite queries
  advisor.py             # Tuning suggestions, hot sequences, periodic summaries
  otlp.py                # OTLP/HTTP JSON export (stdlib only, no SDK)
  render.py              # Rich rendering helpers
  spans.py               # OTel span model: Span dataclass, factory functions
  static.py              # Static/piped output assembly
  tui.py                 # Textual TUI: dashboard + detail, sessions, step, advisor screens

guardrails/              # Optional guardrail scripts (stdlib only, portable)
```

## Database schema

```sql
-- Hook execution telemetry
hook_metrics (id, ts, hook, step, cmd, exit_code,
              duration_ms, real_s, user_s, sys_s,
              branch, sha, host, repo, session, stderr_snippet)

-- Tool usage audit trail
audit_events (id, ts, session, tool, input)
```

## OTel export formats

**`--export`** outputs trends JSON using OpenTelemetry naming conventions:

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

**`--export-spans`** outputs one span per hook execution and tool use:

```json
{
  "schema": "claude.hooks.spans/v1",
  "spans": [...]
}
```

Sensitive fields are redacted by default; use `--include-sensitive` to disable. If `HOOKS_METRICS_OTLP_ENDPOINT` is set, spans are also POSTed to the OTLP endpoint before printing. Set auth headers via `HOOKS_METRICS_OTLP_HEADERS` (`key=value,key2=value2`).

Metric names: `claude.hooks.runs`, `claude.hooks.failures`, `claude.hooks.duration.*`
Attributes: `hook.step`, `vcs.repository`

## License

MIT
