---
title: "Phase 1: Session Correlation + OTEL Span Model + Expanded Event Capture"
phase: 1
date: 2026-02-28
dependencies: none
status: ready
---

# Phase 1: Session Correlation + OTEL Span Model + Expanded Event Capture

## Overview

Today, `hook_metrics` and `audit_events` are two isolated tables — there is no way to correlate hooks that fired within the same Claude Code session, and 12 of 16 event types have zero hooks defined. This phase adds a `session` column to `hook_metrics`, widens audit-logger coverage to all tools, captures 5 new event types, and introduces an OTEL-shaped span model (`spans.py`) that can export both tables as correlated traces. This is the foundation for all subsequent phases.

## Dependencies

- None (this is the foundation for Phases 2-5)

## Implementation

### 1a. Expand event capture coverage

#### Problem

Current hooks only capture events explicitly wired in `settings.json`. The audit-logger PostToolUse matcher (`Bash|Edit|Write|Read|Glob|Grep|WebFetch|WebSearch`) misses `MultiEdit`, `LSP`, `NotebookEdit`, all `mcp__*` tools, `Agent`, `TodoWrite`, etc. And 12 of 16 Claude Code event types have zero hooks defined.

#### Step 1: Widen audit-logger matcher to `*`

Change the PostToolUse audit-logger matcher from an explicit tool list to a wildcard. This captures every tool use (current + future) without maintenance.

#### Step 2: Add event loggers for 5 new events

Create `event-logger.sh` and wire it for each new event type.

**async: true tradeoff analysis:**

- **Option A** (via `hook-metrics.sh` wrapper + `async: true`): Consistent wiring; records timing of `event-logger.sh` in `hook_metrics`; adds `event-log` step that needs `SKIP_HOOKS_PATTERN` exclusion; git context collection adds ~5ms overhead per invocation.
- **Option B** (direct `event-logger.sh` + `async: true`): No `hook_metrics` noise; no git overhead; simpler wiring; no timing visibility for the logger itself.
- **Recommendation**: Use **Option A** for `PostToolUseFailure` (mirrors PostToolUse symmetry; failure tracking is useful). Use **Option B** for `SubagentStart`, `SubagentStop`, `SessionEnd`, `UserPromptSubmit` (lifecycle events where timing the logger itself has no value).

**Constraint**: `SubagentStart` and `SessionEnd` only support `type: "command"` hooks (not prompt or agent hooks). `UserPromptSubmit` and `Stop` output is added as context to Claude — so `event-logger.sh` must exit 0 and print nothing to stdout (stderr only for errors).

#### Complete `settings.json` hooks section after changes

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "*",
        "hooks": [
          "~/.claude/hooks/hook-metrics.sh PostToolUse:audit-logger ~/.claude/hooks/audit-logger.sh"
        ]
      },
      {
        "matcher": "Edit|Write",
        "hooks": [
          "~/.claude/hooks/hook-metrics.sh PostToolUse:mermaid-lint ~/.claude/hooks/mermaid-lint.sh"
        ]
      }
    ],
    "PostToolUseFailure": [
      {
        "matcher": "*",
        "hooks": [
          "~/.claude/hooks/hook-metrics.sh PostToolUseFailure:event-log ~/.claude/hooks/event-logger.sh"
        ],
        "async": true
      }
    ],
    "SubagentStart": [
      {
        "hooks": [
          "~/.claude/hooks/event-logger.sh SubagentStart"
        ],
        "type": "command",
        "async": true
      }
    ],
    "SubagentStop": [
      {
        "hooks": [
          "~/.claude/hooks/event-logger.sh SubagentStop"
        ],
        "async": true
      }
    ],
    "SessionEnd": [
      {
        "hooks": [
          "~/.claude/hooks/event-logger.sh SessionEnd"
        ],
        "type": "command",
        "async": true
      }
    ],
    "UserPromptSubmit": [
      {
        "hooks": [
          "~/.claude/hooks/event-logger.sh UserPromptSubmit"
        ],
        "async": true
      }
    ],
    "Notification": [
      {
        "hooks": [
          "terminal-notifier -title 'Claude Code' -message '$CLAUDE_NOTIFICATION_MESSAGE' -group claude-code -sound Tink"
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          "terminal-notifier -title 'Claude Code' -message 'Turn complete' -group claude-code -sound Blow"
        ]
      }
    ]
  }
}
```

#### Step 3: New script — `event-logger.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

# Lightweight event metadata logger — inserts into audit_events.
# Reads JSON from stdin, extracts session_id + event-specific fields.
# Must exit 0 and produce NO stdout (UserPromptSubmit/Stop output becomes Claude context).

source "$(dirname "$0")/db-init.sh"
_init_hooks_db

EVENT="${1:-unknown}"
input=$(cat)
ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
session=$(echo "$input" | jq -r '.session_id // "unknown"')
# Store event-specific metadata (varies by event type)
metadata=$(echo "$input" | jq -c '{
  event: .hook_event_name,
  cwd: .cwd,
  permission_mode: .permission_mode
} + (if .tool_name then {tool: .tool_name} else {} end)
  + (if .error then {error: (.error | tostring[:200])} else {} end)
  + (if .agent_type then {agent_type: .agent_type, agent_id: .agent_id} else {} end)
  + (if .reason then {reason: .reason} else {} end)' 2>/dev/null || echo '{}')

_db_exec "INSERT INTO audit_events (ts, session, tool, input)
VALUES ('$(_sql_escape "$ts")', '$(_sql_escape "$session")', '$(_sql_escape "$EVENT")', '$(_sql_escape "$metadata")');"

_maybe_prune_hooks_db
exit 0
```

**Event-specific fields extracted per event type** (from Claude Code hook docs):

| Event | Key fields in stdin JSON |
|-------|-------------------------|
| `PostToolUseFailure` | `tool_name`, `tool_input`, `error`, `is_interrupt` |
| `SubagentStart` | `agent_id`, `agent_type` |
| `SubagentStop` | `stop_hook_active`, `agent_id`, `agent_type` |
| `SessionEnd` | `reason` |
| `UserPromptSubmit` | `prompt` (NOT stored — privacy) |

All events share: `session_id`, `hook_event_name`, `cwd`, `permission_mode`, `transcript_path`.

**Limitation**: Plugin hooks (security-guidance, pyright, vtsls LSP checks) run in parallel with user hooks but cannot be wrapped. Their execution is a blind spot — this is a Claude Code architecture constraint with no interception point.

---

### 1b. Schema migration — add `session` to `hook_metrics`

#### `db-init.sh` — add after line 13 (following the existing `repo` migration pattern)

The existing migration (lines 9-13) probes `SELECT repo FROM hook_metrics LIMIT 0` and adds the column if missing. The session migration follows the same pattern:

```bash
# After the existing repo migration block (line 13), before `return 0` (line 14):
if ! sqlite3 "$HOOKS_DB" "SELECT session FROM hook_metrics LIMIT 0" >/dev/null 2>&1; then
  sqlite3 "$HOOKS_DB" "ALTER TABLE hook_metrics ADD COLUMN session TEXT DEFAULT ''" >/dev/null 2>&1 || true
  sqlite3 "$HOOKS_DB" "CREATE INDEX IF NOT EXISTS idx_hm_session ON hook_metrics(session) WHERE session != ''" >/dev/null 2>&1 || true
fi
```

Also add `session TEXT DEFAULT ''` to the CREATE TABLE block (line 40, after `repo`):

```sql
    repo        TEXT DEFAULT '',
    session     TEXT DEFAULT ''
```

#### `hooks_report/db.py` `_connect()` — line 166, after `repo TEXT DEFAULT ''`

Add to the CREATE TABLE in the schema init block:

```python
                        host TEXT DEFAULT '', repo TEXT DEFAULT '',
                        session TEXT DEFAULT ''
```

This is in the `_connect()` method (line 151-176). The change is on line 166, extending the column list.

Also add the partial index after the CREATE TABLE statements (after line 172):

```python
                    CREATE INDEX IF NOT EXISTS idx_hm_session
                        ON hook_metrics(session) WHERE session != '';
```

---

### 1c. Session propagation in `hook-metrics.sh`

#### Extract `session_id` from stdin JSON

Add after line 47 (`host=$(hostname 2>/dev/null || echo "")`), before the timestamp line:

```bash
session=$(jq -r '.session_id // ""' < "$input_file" 2>/dev/null || echo "")
```

This works because:
- `$input_file` still exists on disk (cleanup is in the EXIT trap on line 26)
- `jq` is already a dependency (used by `audit-logger.sh`)
- Git hooks with non-JSON stdin: `jq` fails, `2>/dev/null` suppresses the error, `|| echo ""` provides empty string fallback
- Sub-millisecond cost per invocation
- Note: `jq` is NOT currently used in `hook-metrics.sh` — this is the first usage in this file

#### Update INSERT statement

Change the INSERT on lines 51-68 to include the `session` column:

```bash
sqlite3 "$HOOKS_DB" >/dev/null <<SQL || true
PRAGMA busy_timeout=1000;
INSERT INTO hook_metrics (ts, hook, step, cmd, exit_code, duration_ms, real_s, user_s, sys_s, branch, sha, host, repo, session)
VALUES (
  '$(_sql_escape "$ts")',
  '$(_sql_escape "$HOOK_EVENT")',
  '$(_sql_escape "$HOOK_NAME")',
  '$(_sql_escape "$CMD_ARGS")',
  $exit_code,
  $duration_ms,
  $real,
  $user,
  $sys,
  '$(_sql_escape "$branch")',
  '$(_sql_escape "$sha")',
  '$(_sql_escape "$host")',
  '$(_sql_escape "$repo")',
  '$(_sql_escape "$session")'
);
SQL
```

---

### 1d. New file: `hooks_report/spans.py`

~150 lines. Provides the OTEL-shaped span model and conversion functions.

```python
"""OTEL-shaped span model for hook metrics and audit events.

Converts local SQLite data into spans that follow OTEL naming conventions.
All spans share a trace_id derived from session_id, enabling per-session
trace views in any OTEL-compatible backend.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Optional


@dataclass
class Span:
    """A single span in OTEL format, representing one hook execution or tool use."""
    trace_id: str              # Deterministic from session_id (32 hex chars)
    span_id: str               # Random 16 hex chars
    parent_span_id: str        # Empty for root spans
    name: str                  # "claude.hook.PostToolUse/audit-logger"
    kind: str                  # "hook" | "tool_use"
    start_time: str            # ISO8601 UTC
    end_time: str              # start + duration_ms
    duration_ms: int
    status_code: str           # "OK" | "ERROR"
    attributes: dict = field(default_factory=dict)  # OTEL-style k/v


def trace_id_from_session(session_id: str) -> str:
    """Deterministic trace_id: all spans in same session share one trace.

    Args:
        session_id: Claude Code session UUID.

    Returns:
        32-char hex string (OTEL W3C trace-id format).
        Returns 32 zeros for empty/missing session_id.
    """
    if not session_id:
        return "0" * 32
    return hashlib.md5(session_id.encode()).hexdigest()


def _random_span_id() -> str:
    """Generate a random 16-char hex span_id."""
    return os.urandom(8).hex()


def span_name(hook_event: str, step: str) -> str:
    """Build OTEL-style span name from hook event and step.

    Naming convention:
    - Claude Code events: claude.hook.<Event>/<step>
    - Git hooks: git.hook.<step>
    - Fallback: local.hook.<step>
    """
    from . import config
    if hook_event in config.CLAUDE_EVENTS:
        return f"claude.hook.{hook_event}/{step}"
    if step in config.GIT_HOOKS:
        return f"git.hook.{step}"
    return f"local.hook.{step}"


def status_from_exit_code(code: int, step: str) -> str:
    """Map exit code to OTEL status.

    Args:
        code: Process exit code.
        step: Hook step name (codex-review exit 1 = OK, not error).

    Returns:
        "OK" or "ERROR".
    """
    from . import config
    if step in config.SEMANTIC_EXIT_STEPS and code == 1:
        return "OK"
    if code == 0:
        return "OK"
    return "ERROR"


def _compute_end_time(start_iso: str, duration_ms: int) -> str:
    """Add duration_ms to an ISO8601 timestamp, return ISO8601."""
    from datetime import datetime, timedelta, timezone
    try:
        dt = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
        end = dt + timedelta(milliseconds=duration_ms)
        return end.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    except (ValueError, AttributeError):
        return start_iso


def redact_tool_input(raw_json: str) -> str:
    """Privacy-safe redaction of tool input JSON.

    Keeps: tool name, file_path, command (first token of Bash).
    Redacts: file content, large strings with [REDACTED len=N].

    Args:
        raw_json: JSON string of tool input.

    Returns:
        Redacted JSON string.
    """
    try:
        data = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        return '{"redacted": true}'

    redacted = {}
    for key, val in data.items():
        if key in ("file_path", "path", "pattern", "glob", "command"):
            # For command, keep only first token
            if key == "command" and isinstance(val, str):
                first_token = val.split()[0] if val.split() else ""
                redacted[key] = first_token
            else:
                redacted[key] = val
        elif isinstance(val, str) and len(val) > 100:
            redacted[key] = f"[REDACTED len={len(val)}]"
        else:
            redacted[key] = val
    return json.dumps(redacted)


def hook_metric_to_span(row: tuple) -> Span:
    """Convert a hook_metrics row to a Span.

    Expected row columns: (ts, hook, step, cmd, exit_code, duration_ms,
                           branch, sha, host, repo, session)
    """
    ts, hook, step, cmd, exit_code, duration_ms, branch, sha, host, repo, session = row
    return Span(
        trace_id=trace_id_from_session(session),
        span_id=_random_span_id(),
        parent_span_id="",
        name=span_name(hook, step),
        kind="hook",
        start_time=ts,
        end_time=_compute_end_time(ts, duration_ms),
        duration_ms=duration_ms,
        status_code=status_from_exit_code(exit_code, step),
        attributes={
            "hook.step": step,
            "hook.event": hook,
            "hook.exit_code": exit_code,
            "hook.cmd": cmd,
            "vcs.branch": branch,
            "vcs.sha": sha,
            "vcs.repository": repo,
            "host.name": host,
        },
    )


def audit_event_to_span(row: tuple, redact: bool = True) -> Span:
    """Convert an audit_events row to a Span.

    Expected row columns: (ts, session, tool, input)
    Audit events have 0ms duration (point-in-time).
    """
    ts, session, tool, raw_input = row
    tool_input = redact_tool_input(raw_input) if redact else raw_input
    return Span(
        trace_id=trace_id_from_session(session),
        span_id=_random_span_id(),
        parent_span_id="",
        name=f"claude.tool.{tool}",
        kind="tool_use",
        start_time=ts,
        end_time=ts,
        duration_ms=0,
        status_code="OK",
        attributes={
            "tool.name": tool,
            "tool.input": tool_input,
        },
    )


def export_spans(db, hours: int = 24, redact: bool = True) -> dict:
    """Build OTEL-aligned JSON export of all spans.

    Args:
        db: HooksDB instance.
        hours: Look-back window.
        redact: Whether to redact tool inputs.

    Returns:
        Dict with schema claude.hooks.spans/v1.
    """
    hook_rows, audit_rows = db.spans_raw(hours)
    spans = []
    for row in hook_rows:
        spans.append(asdict(hook_metric_to_span(row)))
    for row in audit_rows:
        spans.append(asdict(audit_event_to_span(row, redact=redact)))

    return {
        "schema": "claude.hooks.spans/v1",
        "generated_at": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat(),
        "hours": hours,
        "span_count": len(spans),
        "spans": spans,
    }
```

---

### 1e. Config additions: `config.py`

Add after line 14 (`SEMANTIC_EXIT_STEPS = {"codex-review"}`):

```python
# Claude Code event type classification
CLAUDE_EVENTS = {
    "PostToolUse", "PreToolUse", "SessionStart", "Stop", "Notification",
    "PostToolUseFailure", "SubagentStart", "SubagentStop", "SessionEnd",
    "UserPromptSubmit",
}

# Git hook names (receive git stdin, not Claude JSON — session always empty)
GIT_HOOKS = {
    "pre-commit", "post-commit", "pre-push",
    "post-checkout", "post-merge", "commit-msg",
}

# Event types captured by event-logger.sh (audit_events rows, not hook_metrics timing)
EVENT_LOG_EVENTS = {
    "PostToolUseFailure", "SubagentStart", "SubagentStop",
    "SessionEnd", "UserPromptSubmit",
}
```

Also update `SKIP_HOOKS_PATTERN` to exclude the new `event-log` step from coverage gap detection (since it is wired via `hook-metrics.sh` for PostToolUseFailure):

```python
SKIP_HOOKS_PATTERN = re.compile(r"^(fake-fail|ok-step|echo|test-hook|main|event-log)$")
```

---

### 1f. CLI: `--export-spans`

#### `cli.py` — add 3 arguments after the `--db` argument (after line 31)

```python
    parser.add_argument(
        "--export-spans",
        action="store_true",
        help="Export OTEL-shaped spans as JSON to stdout",
    )
    parser.add_argument(
        "--span-hours",
        type=int,
        default=24,
        metavar="N",
        help="Look-back window for span export in hours (default: 24)",
    )
    parser.add_argument(
        "--no-redact",
        action="store_true",
        help="Disable tool input redaction in span export (exposes file contents)",
    )
```

#### `__main__.py` — add dispatch before existing `--export` check (before line 13)

```python
    if args.export_spans:
        from .spans import export_spans
        import json
        data = export_spans(db, hours=args.span_hours, redact=not args.no_redact)
        print(json.dumps(data, indent=2))
        return
```

The full `main()` function after changes:

```python
def main():
    args = parse_args()
    db_path = args.db or os.environ.get("CLAUDE_HOOKS_DB") or config.DEFAULT_DB_PATH
    db = HooksDB(db_path)

    if args.export_spans:
        from .spans import export_spans
        import json
        data = export_spans(db, hours=args.span_hours, redact=not args.no_redact)
        print(json.dumps(data, indent=2))
        return

    if args.export:
        from .static import export_json
        export_json(db)
    elif args.static or not sys.stdout.isatty():
        from .static import render_static
        render_static(db, verbose=args.verbose)
    else:
        from .tui import HooksReportApp
        HooksReportApp(db).run()
```

---

### 1g. New query: `db.py`

Add `spans_raw()` method to the `HooksDB` class. This returns raw tuples (not dataclasses) because `spans.py` handles the conversion.

```python
def spans_raw(self, hours: int = 24) -> tuple[list[tuple], list[tuple]]:
    """Fetch raw rows for span conversion.

    Args:
        hours: Look-back window.

    Returns:
        (hook_rows, audit_rows) where:
        - hook_rows: (ts, hook, step, cmd, exit_code, duration_ms,
                      branch, sha, host, repo, session)
        - audit_rows: (ts, session, tool, input)
    """
    cutoff = f"datetime('now', '-{hours} hours')"

    # Check if session column exists (graceful degradation for old DBs)
    try:
        self._connect().execute("SELECT session FROM hook_metrics LIMIT 0")
        session_col = "session"
    except Exception:
        session_col = "'' AS session"

    hook_rows = self._query(f"""
        SELECT ts, hook, step, cmd, exit_code, duration_ms,
               branch, sha, host, repo, {session_col}
        FROM hook_metrics
        WHERE ts >= {cutoff}
        ORDER BY ts
    """)

    audit_rows = self._query(f"""
        SELECT ts, session, tool, input
        FROM audit_events
        WHERE ts >= {cutoff}
        ORDER BY ts
    """)

    return hook_rows, audit_rows
```

---

## Files Changed

| File | Change | Lines affected |
|------|--------|----------------|
| `~/.claude/settings.json` | Widen audit-logger matcher to `*`; add 5 event hooks | Hooks section rewrite |
| `event-logger.sh` | **NEW**: ~20 lines, lightweight event metadata logger | New file |
| `db-init.sh` | +4 lines: session column migration + partial index | After line 13 (migration block) |
| `db-init.sh` | +1 line: session column in CREATE TABLE | Line 40 (schema block) |
| `hook-metrics.sh` | +1 line: jq session extraction | After line 47 |
| `hook-metrics.sh` | +1 column in INSERT statement | Lines 53-68 |
| `hooks_report/spans.py` | **NEW**: ~150 lines, Span dataclass + conversion functions | New file |
| `hooks_report/db.py` | +1 column in CREATE TABLE schema init | Line 166 |
| `hooks_report/db.py` | +1 index in schema init | After line 172 |
| `hooks_report/db.py` | +1 method: `spans_raw()` | End of class |
| `hooks_report/config.py` | +3 sets: CLAUDE_EVENTS, GIT_HOOKS, EVENT_LOG_EVENTS | After line 14 |
| `hooks_report/config.py` | Update SKIP_HOOKS_PATTERN to include `event-log` | Line 15 |
| `hooks_report/cli.py` | +3 arguments: --export-spans, --span-hours, --no-redact | After line 31 |
| `hooks_report/__main__.py` | +5 lines: export-spans dispatch | Before line 13 |

## Verification

```bash
# Verify expanded event capture (after a Claude session with new settings.json)
sqlite3 ~/.claude/hooks.db "SELECT DISTINCT hook FROM hook_metrics ORDER BY hook"
# Expected: PostToolUse, PostToolUseFailure (+ others after use)

# Verify audit-logger now catches ALL tools (not just the original 8)
sqlite3 ~/.claude/hooks.db "SELECT DISTINCT tool FROM audit_events WHERE ts > datetime('now', '-1 hour')"
# Should include MultiEdit, LSP, mcp__* if those tools were used

# Verify new event types in audit_events
sqlite3 ~/.claude/hooks.db "SELECT DISTINCT tool FROM audit_events WHERE tool IN ('SubagentStart','SubagentStop','SessionEnd','UserPromptSubmit','PostToolUseFailure')"

# Verify migration added session column
sqlite3 ~/.claude/hooks.db "PRAGMA table_info(hook_metrics)" | grep session
# Expected: 14|session|TEXT||''|0

# Trigger a hook and verify session is captured
# (run any Claude Code tool, then check last row)
sqlite3 ~/.claude/hooks.db "SELECT session FROM hook_metrics ORDER BY id DESC LIMIT 1"
# Expected: a UUID string (not empty)

# Test span export
~/.claude/hooks/hooks-report.sh --export-spans --span-hours 1 | python3 -m json.tool | head -30
# Expected: JSON with schema "claude.hooks.spans/v1", span_count > 0

# Verify redaction is working
~/.claude/hooks/hooks-report.sh --export-spans | python3 -c "
import json, sys
data = json.load(sys.stdin)
redacted = sum(1 for s in data['spans'] if 'REDACTED' in json.dumps(s.get('attributes', {})))
print(f'{redacted} spans with redacted content out of {data[\"span_count\"]} total')
"

# Verify --no-redact exposes raw content
~/.claude/hooks/hooks-report.sh --export-spans --no-redact --span-hours 1 | python3 -c "
import json, sys
data = json.load(sys.stdin)
print(f'Spans: {data[\"span_count\"]}')
"

# Verify existing report still works (no regressions)
~/.claude/hooks/hooks-report.sh --static | head -20
~/.claude/hooks/hooks-report.sh --export | python3 -m json.tool | head -5
```

## Risks & Notes

- **Historical backfill**: ~45K existing `hook_metrics` rows will have `session = ''`. Timestamp-based correlation with `audit_events` is possible but fragile and not planned for Phase 1. These rows will appear as spans with `trace_id = "0" * 32` (null session).
- **Git hook events**: `pre-commit`, `pre-push`, etc. receive git stdin (not Claude JSON) — `jq` will fail silently and `session` will always be `''`. This is correct behavior.
- **jq dependency**: `hook-metrics.sh` currently does NOT use `jq`. Adding it introduces a new dependency for that script. `jq` is already required by `audit-logger.sh` and is present on all macOS systems with Homebrew. If `jq` is missing, the `|| echo ""` fallback ensures the script still functions (session will be empty).
- **Plugin hook blind spot**: Plugin hooks (security-guidance, pyright, vtsls LSP checks) fire in parallel with user hooks but cannot be wrapped or intercepted. Their timing/failures remain invisible. This is a Claude Code architecture constraint.
- **`event-logger.sh` stdout constraint**: For `UserPromptSubmit` and `Stop` events, any stdout from hooks is added as context to Claude. `event-logger.sh` must produce zero stdout — all output goes to stderr or is suppressed.
- **Span ID non-determinism**: `span_id` uses `os.urandom()`, so repeated `--export-spans` calls produce different span IDs for the same data. This is standard OTEL behavior — span IDs are not meant to be stable identifiers.
- **`async: true` fire-and-forget**: Async hooks have no error reporting channel. If `event-logger.sh` fails, the failure is silent. Mitigated by the script's simplicity and the `|| true` pattern on DB writes.
- **SKIP_HOOKS_PATTERN update**: Adding `event-log` to the pattern means the coverage gap detector won't flag it as an uncovered step. This is intentional — `event-log` is infrastructure, not a user-facing hook.
