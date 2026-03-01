---
title: "Phase 1: Session Correlation + OTEL Span Model + Expanded Event Capture"
type: feat
date: 2026-03-01
supersedes: docs/plans/2026-02-28-feat-session-correlation-otel-spans-plan.md
status: ready
dependencies: none
---

# Phase 1: Session Correlation + OTEL Span Model + Expanded Event Capture

## Overview

Phase 1 is the dependency anchor for the entire OTEL observability roadmap. It adds three things
that everything else requires:

1. **Session column in `hook_metrics`** — links hook executions to the Claude session that
   triggered them, enabling per-session analysis and OTEL span correlation
2. **Expanded event capture** — extend `audit-logger.sh` with an optional event-type argument
   to capture 5 lifecycle events currently invisible to the system; no new script needed
3. **Span model** — new `hooks_report/spans.py` converts DB rows to OTEL-shaped spans,
   enabling `--export-spans` for downstream consumers

This plan supersedes `2026-02-28-feat-session-correlation-otel-spans-plan.md` with updated line
references post-refactor and a required prerequisite fix to `_query()`.

---

## Enhancement Summary

**Deepened on:** 2026-03-01
**Sections enhanced:** prerequisite + 1a–1g + risks
**Research agents:** OTLP/OTel spec, SQLite patterns, Bash security, Privacy/redaction, Security sentinel, Performance oracle, Code simplicity reviewer, cc-validate hook config

### Key Improvements

1. **OTLP JSON wire format corrections** — attribute values need typed wrappers (`{"stringValue": "..."}`, `{"intValue": "42"}`); `parent_span_id` must be omitted (not `null`/`""`) for root spans; all field names are camelCase on the wire
2. **SQLite datetime parameterization** — use `datetime('now', ?)` with the full modifier string `"-24 hours"`, not `? || ' hours'` concatenation
3. **Bash security: two HIGH-severity issues** — unbounded `input=$(cat)` in `audit-logger.sh` and unquoted heredoc in `_db_exec` allow shell injection; fixed with temp-file pattern and `tr -d` sanitization
4. **Privacy: `--no-redact` is backwards** — replace with opt-in `--include-sensitive`; redact at export boundary; `file_path` should export as basename only; `host.name` should be hashed
5. **Simplicity cuts** — remove `events: list` YAGNI field, remove `parent_span_id` from dataclass (always `""`), use `f"{row_id:016x}"` over SHA256 for span IDs, remove `--span-hours` YAGNI flag, remove `EVENT_LOG_STEPS` duplicate
6. **Hook config corrections** — `UserPromptSubmit` has no `matcher` support (omit it); other new events (PostToolUseFailure, SubagentStart/Stop, SessionEnd) DO support matchers; fix TOOL extraction fallback to `.tool_name // .agent_type // .hook_event_name`
7. **Performance guards** — add `LIMIT` to `spans_raw()`, enforce `head -c 65536` stdin bound; use PRAGMA check (not `HooksDBError` catch) for graceful degradation
8. **`$CLAUDE_SESSION_ID` env var** — available in all hook commands (v2.1.9+); `hook-metrics.sh` uses it as primary session source with jq fallback, avoiding JSON parsing overhead

### New Considerations Discovered

- `intValue` in OTLP JSON is a **string** (e.g., `{"intValue": "42"}`), not a number — backends silently drop integer attributes that aren't strings
- The existing `idx_hm_ts` index already covers ts-range queries in `spans_raw()`; the partial session index serves only session-scoped lookups (Phase 2)
- `audit_events.input` rows already reach **114KB in production** — the `head -c 65536` guard is urgent, not speculative
- `$CLAUDE_SESSION_ID` env var is set by Claude Code in all hook commands — simpler than parsing stdin JSON for session ID in `hook-metrics.sh`
- `UserPromptSubmit` is the only event among the 5 new ones that has no `matcher` support; the other 4 do support matchers (PostToolUseFailure→tool_name, SubagentStart/Stop→agent_type, SessionEnd→reason)
- PRAGMA `table_info` check is cleaner than catching `HooksDBError` for graceful degradation in `spans_raw()`

---

## Critical Prerequisite: Add Params to `_query()` / `_query_one()`

The Phase 2 plan review flagged SQL injection in plan code using f-string interpolation for
user-supplied values (`step`, `repo`, `session_id`). The fix is parameterized queries. Before
any Phase 1 span queries land, update `_query()` and `_query_one()` to accept an optional
params list.

**File:** `hooks_report/db.py`

```python
# Current signatures (lines ~182-192)
def _query(self, sql: str) -> list[tuple]:
    try:
        return self._connect().execute(sql).fetchall()
    except sqlite3.Error as e:
        raise HooksDBError(f"{self.path}: {e} — SQL: {sql[:200]}") from e

def _query_one(self, sql: str) -> Optional[tuple]:
    try:
        return self._connect().execute(sql).fetchone()
    except sqlite3.Error as e:
        raise HooksDBError(f"{self.path}: {e} — SQL: {sql[:200]}") from e
```

Change to:

```python
def _query(self, sql: str, params: tuple = ()) -> list[tuple]:
    try:
        return self._connect().execute(sql, params).fetchall()
    except sqlite3.Error as e:
        raise HooksDBError(f"{self.path}: {e} — SQL: {sql[:200]}") from e

def _query_one(self, sql: str, params: tuple = ()) -> Optional[tuple]:
    try:
        return self._connect().execute(sql, params).fetchone()
    except sqlite3.Error as e:
        raise HooksDBError(f"{self.path}: {e} — SQL: {sql[:200]}") from e
```

Default `params=()` is backward-compatible — all 25+ existing callers pass no params and
continue to work unchanged. New callers in Phase 1 (and Phases 2–4) use bound parameters.

> **Note:** `tuple` is preferred over `list | tuple` — clearer and avoids the union syntax that requires Python 3.10+ without `from __future__ import annotations`. The backward-compatible default and error re-raise pattern are sound; no other structural changes needed.

---

## Implementation Sections

### 1a. Expand Event Capture

Currently `audit-logger.sh` is wired only to `PostToolUse` events. Five high-value lifecycle
events are invisible to the system:

| Event | Why it matters |
|---|---|
| `PostToolUseFailure` | Tool errors — correlates with hook failures |
| `SubagentStart` | Parallel workload detection |
| `SubagentStop` | Parallel workload completion + duration |
| `SessionEnd` | Session boundary — enables session-scoped metrics |
| `UserPromptSubmit` | Conversation cadence, session activity signal |

**File:** `~/.claude/settings.json` (not in repo — document in CLAUDE.md deploy section)

**No new script needed.** All 5 event types are handled by the existing `audit-logger.sh` with an optional `$1` event type argument. Existing PostToolUse invocations pass no argument and continue to work unchanged.

```json
{
  "hooks": {
    "PostToolUse": [
      { "matcher": "*", "hooks": [{ "type": "command", "command": "~/.claude/hooks/audit-logger.sh" }] }
    ],
    "PostToolUseFailure": [
      { "matcher": "*", "hooks": [{ "type": "command", "command": "~/.claude/hooks/audit-logger.sh PostToolUseFailure" }] }
    ],
    "SubagentStart": [
      { "matcher": "*", "hooks": [{ "type": "command", "command": "~/.claude/hooks/audit-logger.sh SubagentStart" }] }
    ],
    "SubagentStop": [
      { "matcher": "*", "hooks": [{ "type": "command", "command": "~/.claude/hooks/audit-logger.sh SubagentStop" }] }
    ],
    "SessionEnd": [
      { "matcher": "*", "hooks": [{ "type": "command", "command": "~/.claude/hooks/audit-logger.sh SessionEnd" }] }
    ],
    "UserPromptSubmit": [
      { "hooks": [{ "type": "command", "command": "~/.claude/hooks/audit-logger.sh UserPromptSubmit" }] }
    ]
  }
}
```

UserPromptSubmit omits `matcher` — it has no matcher support. The 4 other new events keep `matcher: "*"` (PostToolUseFailure matches tool_name, SubagentStart/Stop match agent_type, SessionEnd matches reason).

**Modified file:** `audit-logger.sh` (source: repo root, deploy to `~/.claude/hooks/`)

```bash
#!/usr/bin/env bash
set -euo pipefail

# shellcheck source=db-init.sh
source "$(dirname "$0")/db-init.sh"
_init_hooks_db

# Optional event type prefix (e.g. PostToolUseFailure). Blank for PostToolUse.
EVENT_TYPE="${1:-}"

# Tee stdin to temp file — bounded store, full-fidelity passthrough
TMPFILE=$(mktemp)
trap 'rm -f "$TMPFILE"' EXIT
cat > "$TMPFILE"

# jq guard — exit 0 so missing jq never blocks Claude Code
command -v jq >/dev/null 2>&1 || { cat "$TMPFILE"; exit 0; }

ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Fallback chain covers PostToolUse (.tool_name), SubagentStart/Stop (.agent_type),
# SessionEnd/UserPromptSubmit (.hook_event_name)
tool=$(head -c 65536 "$TMPFILE" | jq -r '.tool_name // .agent_type // .hook_event_name // "unknown"')

# Prepend event type for non-PostToolUse events (e.g. "PostToolUseFailure:Write")
[ -n "$EVENT_TYPE" ] && tool="${EVENT_TYPE}:${tool}"

# Strip shell-injectable chars before heredoc interpolation
session=$(head -c 65536 "$TMPFILE" | jq -r '.session_id // "unknown"' | tr -d '`$\n\r')
tool=$(printf '%s' "$tool" | tr -d '`$\n\r')

# Store truncated input (tool_input only, max 4KB)
tool_input=$(head -c 4096 "$TMPFILE" | jq -c '.tool_input // {}' 2>/dev/null || echo '{}')

sqlite3 "$HOOKS_DB" >/dev/null <<SQL || true
PRAGMA busy_timeout=1000;
INSERT INTO audit_events (ts, session, tool, input)
VALUES ('$(_sql_escape "$ts")', '$(_sql_escape "$session")', '$(_sql_escape "$tool")', '$(_sql_escape "$tool_input")');
SQL

_maybe_prune_hooks_db

# Full-fidelity passthrough — echo original, not truncated version
cat "$TMPFILE"
exit 0
```

Note: `tool` stores `EVENT_TYPE:tool_name` (e.g. `PostToolUseFailure:Write`) so event type is queryable without JSON parsing. `input` stores `tool_input` only, truncated to 4KB, preventing the 114KB bloat observed in production `audit_events` rows. The temp-file pattern ensures Claude Code always receives the original untruncated payload.

---

### 1b. Schema Migration — `session` Column in `hook_metrics`

**File:** `db-init.sh`

The migration probe pattern already exists (used for the `repo` column). Add after the
existing `ALTER TABLE` probes:

```bash
# In _init_hooks_db(), after the repo column probe:
_q "SELECT session FROM hook_metrics LIMIT 0" 2>/dev/null || \
  _db_exec "ALTER TABLE hook_metrics ADD COLUMN session TEXT DEFAULT ''"

# Add partial index for session lookups (non-empty only)
_db_exec "CREATE INDEX IF NOT EXISTS idx_hook_metrics_session
  ON hook_metrics(session) WHERE session != ''"
```

**File:** `hooks_report/db.py` — `_connect()` method

The `_connect()` method also initializes schema for empty DBs. Add the `session` column to
the `CREATE TABLE IF NOT EXISTS hook_metrics` statement so new DBs get it from the start:

```python
# In the CREATE TABLE statement (currently ends with repo TEXT DEFAULT ''):
CREATE TABLE IF NOT EXISTS hook_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL, hook TEXT NOT NULL, step TEXT NOT NULL,
    cmd TEXT NOT NULL, exit_code INTEGER NOT NULL,
    duration_ms INTEGER NOT NULL, real_s REAL NOT NULL,
    user_s REAL NOT NULL, sys_s REAL NOT NULL,
    branch TEXT DEFAULT '', sha TEXT DEFAULT '',
    host TEXT DEFAULT '', repo TEXT DEFAULT '',
    session TEXT DEFAULT ''                    -- ← add this
);
CREATE INDEX IF NOT EXISTS idx_hook_metrics_session
  ON hook_metrics(session) WHERE session != '';
```

Historical rows will have `session = ''`. Queries must treat empty string as "no session"
(use `WHERE session != ''` filters where relevant).

The partial index serves session-scoped lookups (Phase 2). Since both `hook-metrics.sh` and `audit-logger.sh` call `_init_hooks_db` on every invocation, the migration runs automatically on the first hook fire after deploy — no explicit migration step needed. Flag composite `(session, ts)` index for Phase 2 schema planning.

---

### 1c. Session Propagation in `hook-metrics.sh`

Currently `hook-metrics.sh` tees stdin to a temp file and passes it to the downstream script,
but never inspects it for `session_id`.

**File:** `hook-metrics.sh`

**Session ID extraction** — add immediately after the temp file is written, before the downstream script runs:

```bash
# Primary: use env var set by Claude Code (v2.1.9+)
# Fallback: parse from stdin JSON (for older Claude or git hooks)
SESSION_ID="${CLAUDE_SESSION_ID:-}"
if [ -z "$SESSION_ID" ]; then
  SESSION_ID=$(jq -r '.session_id // ""' "$input_file" 2>/dev/null | tr -d '`$\n\r' || echo "")
fi
```

**Variable sanitization** — add before the INSERT, showing updated git context extraction:

```bash
# Sanitize shell variables before heredoc interpolation — prevents $() execution
branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null | tr -d '`$\n\r' || echo "")
sha=$(git rev-parse --short HEAD 2>/dev/null || echo "")
repo=$(git rev-parse --show-toplevel 2>/dev/null | tr -d '`$\n\r' || echo "")
host=$(hostname 2>/dev/null | tr -d '`$\n\r' || echo "")
```

**INSERT block** — includes `BEGIN IMMEDIATE` and the `session` column:

```bash
sqlite3 "$HOOKS_DB" >/dev/null <<SQL || true
PRAGMA busy_timeout=1000;
BEGIN IMMEDIATE;
INSERT INTO hook_metrics
  (ts, hook, step, cmd, exit_code, duration_ms, real_s, user_s, sys_s, branch, sha, host, repo, session)
  VALUES (
    '$(_sql_escape "$TS")',
    '$(_sql_escape "$HOOK_EVENT")',
    '$(_sql_escape "$STEP_NAME")',
    '$(_sql_escape "$CMD_ARGS")',
    $EXIT_CODE, $DURATION_MS, $REAL_S, $USER_S, $SYS_S,
    '$(_sql_escape "$branch")',
    '$(_sql_escape "$sha")',
    '$(_sql_escape "$host")',
    '$(_sql_escape "$repo")',
    '$(_sql_escape "$SESSION_ID")'
  );
COMMIT;
SQL
```

`hook-metrics.sh` uses a direct `sqlite3` heredoc (lines 51-69), not `_db_exec`. `BEGIN IMMEDIATE` prevents silent `SQLITE_BUSY` data loss when multiple hooks fire simultaneously (parallel subagents). `tr -d` strips shell-injectable characters before they reach the unquoted heredoc.

---

### 1d. New Module: `hooks_report/spans.py`

New ~130-line module. Converts DB rows to flat span dicts in `claude.hooks.spans/v1` format. No external OTel SDK dependency. Designed for human readability and LLM analysis — not OTLP wire format (that defers to Phase 5).

**File:** `hooks_report/spans.py` (new)

```python
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from . import config


@dataclass
class Span:
    """Custom span record for claude.hooks.spans/v1. No external SDK dependency."""
    trace_id: str           # 32-char hex; derived from session_id via SHA256
    span_id: str            # 16-char hex; prefix byte + row id (human-readable, sort-stable)
    name: str               # "hook.{step}" or "tool.{tool_name}"
    kind: int               # 1=INTERNAL (hooks), 3=CLIENT (tools)
    start_time_unix_nano: int
    end_time_unix_nano: int
    status_code: int        # 0=UNSET, 1=OK, 2=ERROR
    attributes: dict        # flat key/value pairs


def trace_id_from_session(session_id: str) -> str:
    """Deterministic 32-char hex trace_id from session_id."""
    if not session_id:
        return "0" * 32
    return hashlib.sha256(session_id.encode()).hexdigest()[:32]


def span_id_from_row_id(row_id: int, prefix: str = "h") -> str:
    """Deterministic 16-char hex span_id. Prefix byte + row id — human-readable and sort-stable."""
    prefix_byte = ord(prefix[0])
    return f"{prefix_byte:02x}{row_id:014x}"


def _ts_to_nanos(ts_str: str) -> int:
    """Convert SQLite TEXT timestamp to Unix nanoseconds. Returns 0 on corrupt input."""
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        try:
            dt = datetime.fromisoformat(ts_str).replace(tzinfo=timezone.utc)
        except (ValueError, AttributeError):
            return 0  # skip corrupt timestamps; caller can filter on 0
    return int(dt.timestamp() * 1_000_000_000)


def _redact_tool_input(tool: str, input_json: str) -> str:
    """Return privacy-safe summary of tool input. Never returns file contents."""
    import json
    try:
        inp = json.loads(input_json)
    except (json.JSONDecodeError, TypeError):
        return "{}"
    safe: dict = {}
    if "command" in inp:
        # Keep only first token (command name, not args); guard against whitespace-only strings
        safe["command"] = (inp["command"].split() or [""])[0] if inp["command"] else ""
    if "file_path" in inp:
        # Basename only — full paths leak directory structure
        safe["file_path"] = os.path.basename(inp["file_path"])
    if "tool_name" in inp:
        safe["tool_name"] = inp["tool_name"]
    return json.dumps(safe)


def _hash_host(host: str) -> str:
    """One-way hash of hostname — identifies machine type without exposing identity."""
    return hashlib.sha256(host.encode()).hexdigest()[:12]


def hook_metric_to_span(row: tuple, redact: bool = True) -> Span:
    """Convert a hook_metrics row to a Span.

    Row order: id, ts, hook, step, cmd, exit_code, duration_ms,
               real_s, user_s, sys_s, branch, sha, host, repo, session
    """
    (row_id, ts, hook, step, cmd, exit_code, duration_ms,
     real_s, user_s, sys_s, branch, sha, host, repo, session) = row

    start_ns = _ts_to_nanos(ts)
    end_ns = start_ns + int(duration_ms) * 1_000_000

    status_code = 2 if exit_code != 0 else 1  # ERROR or OK

    # Privacy defaults match --export baseline: no raw hostnames or full paths
    repo_display = os.path.basename(repo.rstrip("/")) if repo else ""

    attrs: dict = {
        "hook.step": step,
        "hook.event": hook,
        "hook.exit_code": exit_code,
        "hook.duration_ms": int(duration_ms),
        "vcs.branch": branch,
        "vcs.commit_sha": sha,
        "vcs.repository": repo_display if redact else repo,
        "host.name": _hash_host(host) if redact else host,
    }
    if not redact:
        attrs["hook.cmd"] = cmd

    return Span(
        trace_id=trace_id_from_session(session),
        span_id=span_id_from_row_id(row_id, "h"),
        name=f"hook.{step}",
        kind=1,  # INTERNAL
        start_time_unix_nano=start_ns,
        end_time_unix_nano=end_ns,
        status_code=status_code,
        attributes=attrs,
    )


def audit_event_to_span(row: tuple, redact: bool = True) -> Span:
    """Convert an audit_events row to a Span.

    Row order: id, ts, session, tool, input
    """
    row_id, ts, session, tool, input_json = row

    start_ns = _ts_to_nanos(ts)
    end_ns = start_ns  # tool-use events have no duration

    tool_input = _redact_tool_input(tool, input_json) if redact else input_json

    attrs: dict = {
        "tool.name": tool,
        "tool.input": tool_input,
    }

    return Span(
        trace_id=trace_id_from_session(session),
        span_id=span_id_from_row_id(row_id, "a"),
        name=f"tool.{tool}",
        kind=3,  # CLIENT
        start_time_unix_nano=start_ns,
        end_time_unix_nano=end_ns,
        status_code=1,  # OK — failures captured in PostToolUseFailure events
        attributes=attrs,
    )


def spans_to_dict(spans: list[Span]) -> dict:
    """Serialize spans to claude.hooks.spans/v1 JSON.

    Flat structure designed for human readability and LLM analysis.
    Not OTLP wire format — defer camelCase + typed attribute wrappers to Phase 5.
    """
    return {
        "schema": "claude.hooks.spans/v1",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "spans": [
            {
                "trace_id": s.trace_id,
                "span_id": s.span_id,
                "name": s.name,
                "kind": s.kind,
                "start_ns": s.start_time_unix_nano,
                "end_ns": s.end_time_unix_nano,
                "status": s.status_code,
                "attributes": s.attributes,
            }
            for s in spans
        ],
    }
```

---

### 1e. Config Additions

**File:** `hooks_report/config.py`

Add after `SEMANTIC_EXIT_STEPS`:

```python
# Event types captured by audit-logger.sh
CLAUDE_EVENTS: set[str] = {
    "PostToolUse",
    "PostToolUseFailure",
    "SubagentStart",
    "SubagentStop",
    "SessionEnd",
    "UserPromptSubmit",
}

# Hook steps triggered by git operations (not Claude tool-use)
GIT_HOOKS: set[str] = {"pre-commit", "commit-msg", "prepare-commit-msg"}
```

Also update `SKIP_HOOKS_PATTERN` to exclude the `event-log` step name if used:

```python
SKIP_HOOKS_PATTERN = re.compile(r"^(fake-fail|ok-step|echo|test-hook|main|event-log)$")
```

Note: `SKIP_HOOKS_PATTERN` must remain `re.compile()` — the existing code at `config.py:15` uses `re.compile()` and `db.py:477` calls `re.fullmatch(config.SKIP_HOOKS_PATTERN, step)`. This edit extends the existing compiled pattern rather than changing its type.

---

### 1f. CLI: `--export-spans` Flag

**File:** `hooks_report/cli.py`

Add two arguments after the existing `--export` argument:

```python
p.add_argument(
    "--export-spans",
    action="store_true",
    help="Export claude.hooks.spans/v1 JSON to stdout",
)
p.add_argument(
    "--include-sensitive",
    action="store_true",
    help="Include full tool inputs, raw hostnames, and full paths in span export (off by default)",
)
```

**File:** `hooks_report/__main__.py`

Add dispatch before the existing `if args.export:` branch:

```python
if args.export_spans:
    from .spans import hook_metric_to_span, audit_event_to_span, spans_to_dict
    hook_rows = db.spans_raw()
    audit_rows = db.audit_spans_raw()
    spans = (
        [hook_metric_to_span(r, redact=not args.include_sensitive) for r in hook_rows]
        + [audit_event_to_span(r, redact=not args.include_sensitive) for r in audit_rows]
    )
    spans.sort(key=lambda s: s.start_time_unix_nano)
    import json
    print(json.dumps(spans_to_dict(spans), indent=2))
```

Move `import json` to top-level imports in `__main__.py` since both export paths need it.

---

### 1g. New DB Queries: `spans_raw()` and `audit_spans_raw()`

**File:** `hooks_report/db.py`

Add after `export_data()` (currently the last method):

```python
def _has_session_column(self) -> bool:
    """Check whether hook_metrics has the session column (added in Phase 1)."""
    rows = self._query("PRAGMA table_info(hook_metrics)")
    return any(r[1] == "session" for r in rows)

def spans_raw(self, hours: int = 24, limit: int = 10000) -> list[tuple]:
    """Return hook_metrics rows for span export.

    Gracefully degrades on old DBs without the session column — pads empty string.
    Uses PRAGMA check (not HooksDBError catch) to distinguish schema issues from
    other DB errors.
    """
    has_session = self._has_session_column()  # cache — schema is stable within a call
    col_sel = (
        "id, ts, hook, step, cmd, exit_code, duration_ms, "
        "real_s, user_s, sys_s, branch, sha, host, repo"
        + (", session" if has_session else "")
    )
    rows = self._query(
        f"SELECT {col_sel} FROM hook_metrics "
        "WHERE ts > datetime('now', ?) ORDER BY ts LIMIT ?",
        [f"-{hours} hours", limit],
    )
    if not has_session:
        return [r + ("",) for r in rows]
    return rows

def audit_spans_raw(self, hours: int = 24, limit: int = 10000) -> list[tuple]:
    """Return audit_events rows for span export.

    Truncates input to 4KB in the query — production rows can reach 114KB.
    """
    return self._query(
        "SELECT id, ts, session, tool, substr(input, 1, 4096) "
        "FROM audit_events "
        "WHERE ts > datetime('now', ?) ORDER BY ts LIMIT ?",
        [f"-{hours} hours", limit],
    )
```

Note: Uses full SQLite modifier string form `f"-{hours} hours"` (e.g. `"-24 hours"`) — unambiguous and matches SQLite docs. `audit_spans_raw()` truncates `input` at 4KB in the query to avoid loading 114KB+ production payloads into memory.

---

## Verification

```bash
# 1. Schema migration — verify session column added
sqlite3 ~/.claude/hooks.db "PRAGMA table_info(hook_metrics);" | grep session

# 2. Session propagation — run a hook manually and check the row
echo '{"session_id":"test-session-123","tool_name":"Bash"}' | \
  ~/.claude/hooks/hook-metrics.sh PostToolUse:test-step echo ok
sqlite3 ~/.claude/hooks.db \
  "SELECT session, step FROM hook_metrics ORDER BY id DESC LIMIT 1;"
# Expected: test-session-123 | test-step

# 3. Expanded event capture — test audit-logger with event type arg
echo '{"session_id":"abc","tool_name":"Write"}' | \
  ~/.claude/hooks/audit-logger.sh PostToolUseFailure
sqlite3 ~/.claude/hooks.db \
  "SELECT tool, session FROM audit_events ORDER BY id DESC LIMIT 1;"
# Expected: PostToolUseFailure:Write | abc

# 4. Backward compat — existing PostToolUse invocation (no arg) still works
echo '{"session_id":"abc","tool_name":"Bash","tool_input":{"command":"ls"}}' | \
  ~/.claude/hooks/audit-logger.sh
sqlite3 ~/.claude/hooks.db \
  "SELECT tool, session FROM audit_events ORDER BY id DESC LIMIT 1;"
# Expected: Bash | abc

# 5. Span export — valid JSON
~/.claude/hooks/hooks-report.sh --export-spans | python3 -m json.tool > /dev/null
echo "exit: $?"  # Expected: 0

# 6. Span export — verify flat structure and count
~/.claude/hooks/hooks-report.sh --export-spans | \
  python3 -c "import json,sys; d=json.load(sys.stdin); \
  print(len(d['spans']), 'spans, schema:', d['schema'])"

# 7. include-sensitive flag
~/.claude/hooks/hooks-report.sh --export-spans --include-sensitive | \
  python3 -c "import json,sys; d=json.load(sys.stdin); \
  tool_spans=[s for s in d['spans'] if s['name'].startswith('tool.')]; \
  print(tool_spans[0]['attributes'].get('tool.input','(none)') if tool_spans else 'no tool spans')"

# 8. Backward compat — static and verbose still work
~/.claude/hooks/hooks-report.sh --static 2>&1 | tail -3
~/.claude/hooks/hooks-report.sh --export | python3 -m json.tool > /dev/null && echo "export: ok"

# 9. Old-DB graceful degradation — test spans_raw fallback
CLAUDE_HOOKS_DB=/tmp/old-test.db python3 -c "
from hooks_report.db import HooksDB
db = HooksDB('/tmp/old-test.db')
rows = db.spans_raw()
print('rows:', len(rows), '— session col present:', len(rows[0]) == 15 if rows else 'n/a (empty)')
"
```

---

## Deploy

```bash
# 1. Deploy Python package
rsync -a --delete hooks_report/ ~/.claude/hooks/hooks_report/

# 2. Deploy bash scripts
# Note: both hook-metrics.sh and audit-logger.sh call _init_hooks_db on startup,
# so the session column migration runs automatically on first hook invocation.
install -m 755 hooks-report.sh ~/.claude/hooks/hooks-report.sh
install -m 755 hook-metrics.sh ~/.claude/hooks/hook-metrics.sh
install -m 755 audit-logger.sh ~/.claude/hooks/audit-logger.sh
install -m 755 db-init.sh ~/.claude/hooks/db-init.sh

# 3. Verify schema migration ran (after any hook fires, or run db-init.sh manually)
~/.claude/hooks/db-init.sh
sqlite3 ~/.claude/hooks.db "PRAGMA table_info(hook_metrics);" | grep session

# 4. Update settings.json — add 5 new event hook entries (see §1a for config)
```

---

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| `jq` absent on some machines | Low | `jq` already required by `audit-logger.sh`; document as system dep |
| `$CLAUDE_SESSION_ID` env var version | Low | Requires Claude Code v2.1.9+; fallback jq parsing handles older versions transparently |
| Old DB: `session` column missing for `spans_raw()` | Certain for existing DBs | Graceful fallback in `spans_raw()` pads empty string |
| `datetime('now', ? \|\| ' hours')` SQLite syntax | Low | Prefer `datetime('now', ?)` with full modifier string `"-24 hours"` — unambiguous and matches SQLite docs |
| Large span exports (millions of rows) | Medium | `spans_raw()` enforces `LIMIT 10000`; no CLI flag needed |
| `audit_events.input` bloat from `*` matcher | **High** | Prod rows already 114KB; `audit-logger.sh` uses `head -c 65536` + `substr(input, 1, 4096)` in `audit_spans_raw()` |
| Overlapping session IDs from parallel subagents | Present by design | Both subagents share the parent `session_id` — grouping is correct OTEL behavior |
| Heredoc injection in `_db_exec` (pre-existing) | **High** | Strip `$` and backticks from shell variables before interpolation; affects `BRANCH`, `REPO`, `SESSION_ID` |
| OTLP JSON format (Phase 5) | Low | `spans_to_dict()` outputs `claude.hooks.spans/v1` custom format — not OTLP wire format; full OTLP compliance deferred to Phase 5 with `opentelemetry-sdk` |
| `--include-sensitive` security | Medium | Opt-in flag; document that output includes raw hostnames, full paths, tool inputs — not for sharing |
| Concurrent INSERTs in WAL mode | Medium | Add `BEGIN IMMEDIATE` to `hook-metrics.sh` INSERT to prevent silent `SQLITE_BUSY` data loss |

---

# Validation Results

**Validated:** 2026-03-01
**Verdict:** CAUTION

Four parallel analysis agents (code-explorer, code-reviewer, silent-failure-hunter, architectural-validator) examined the plan against the actual codebase. The plan is structurally sound but has a systemic problem: **critical fixes exist only in "Research Insights" sections, not in the canonical code blocks**. An implementer following the code blocks will ship known vulnerabilities.

## Issues Found

### Critical (Must Address)

- **Canonical code vs Research Insights disconnect**: Every code block in sections 1a, 1c, 1d, 1f, 1g contains known vulnerabilities (unbounded stdin, missing jq guard, broad error catch, OTLP format errors) that are only fixed in the Research Insights subsections. The Research Insights read as advisory, not prescriptive. Merge all mitigations into the canonical code blocks before implementation.
  - _Impact_: Implementer ships vulnerable code
  - _Mitigation_: Rewrite each section's code block to be the "hardened" version, move research notes to footnotes
  - _Files affected_: All sections (1a–1g)

- **`SKIP_HOOKS_PATTERN` type mismatch**: Plan §1e proposes `SKIP_HOOKS_PATTERN: str = r"..."`. Actual code at `config.py:15` uses `re.compile(r"^(...)$")`. The plan silently downgrades a compiled regex to a raw string. While `re.fullmatch()` at `db.py:477` accepts both, the type change is gratuitous and breaks the established pattern.
  - _Impact_: Type inconsistency, diverges from codebase conventions
  - _Mitigation_: Keep `re.compile()` — just add `|event-log` to the existing pattern
  - _File_: `hooks_report/config.py:15`

- **`spans_to_dict()` produces invalid OTLP**: The output uses snake_case keys, bare attribute dicts, and sets `parent_span_id: None` for root spans. OTLP requires camelCase, typed key-value lists (`{"key":"k","value":{"stringValue":"v"}}`), and omission (not null) of `parentSpanId` for root spans. Result: output looks like OTLP but is rejected by every OTLP collector.
  - _Impact_: Data silently dropped by any OTLP backend (Jaeger, Tempo, etc.)
  - _Mitigation_: See "Simplification Opportunities" — use clean custom format for Phase 1, defer OTLP compliance to Phase 5
  - _File_: Plan §1d `spans_to_dict()`

- **Deploy ordering creates silent data loss window**: Plan's deploy section (§Deploy) deploys `hook-metrics.sh` at step 2, runs schema migration at step 3. If a hook fires between steps 2 and 3, the INSERT includes `session` column that doesn't exist yet. The `|| true` at `hook-metrics.sh:51` silently swallows the error — metrics are lost.
  - _Impact_: Silent data loss during deployment
  - _Mitigation_: Reorder deploy: run `db-init.sh` migration **before** deploying updated scripts. Or have `hook-metrics.sh` call `_init_hooks_db` (it already does at line 6 — so the fix is to add the session migration to `_init_hooks_db` in step 1)
  - _File_: Plan §Deploy steps 2-3

- **`event-logger.sh` echo truncation**: If `head -c 65536` is applied (per Research Insights), `echo "$PAYLOAD"` echoes truncated JSON back to Claude Code. For payloads >64KB, downstream hooks receive invalid JSON. This is not addressed anywhere in the plan.
  - _Impact_: Downstream hooks in chain receive corrupted payload
  - _Mitigation_: Tee stdin to temp file (like `hook-metrics.sh` does), apply size limit only to what is stored in DB, echo original from file
  - _File_: Plan §1a `event-logger.sh`

### High Risk (Should Address)

- **`event-logger.sh` is unjustified duplication**: `audit-logger.sh` (24 lines) already does exactly what `event-logger.sh` proposes: read JSON → extract fields with jq → insert into `audit_events` → echo stdin. Same table, same columns, same dependencies. The only difference is the `TOOL` fallback chain, which is one jq expression.
  - _Impact_: Two scripts with identical logic to maintain, two scripts with the same heredoc injection vulnerability, 4 bash scripts to deploy instead of 3
  - _Recommendation_: Extend `audit-logger.sh` to accept an optional `$1` event type argument. When present, prepend it to the `tool` column. One script, one set of security fixes.

- **Privacy model inconsistency between `--export` and `--export-spans`**: `--export` (existing, `db.py:883-1026`) strips username from repo paths and does not output hostnames, commands, or file paths. `--export-spans` default mode outputs `host.name` (raw hostname), `vcs.repository` (full path), `vcs.branch`, `vcs.commit_sha` — all absent from `--export`. The two export modes have different privacy baselines with no documented privacy tier model.
  - _Impact_: Users may unknowingly expose sensitive data via `--export-spans` that `--export` kept private
  - _Recommendation_: Define privacy tiers before implementation. In default spans mode: hash `host.name`, use repo basename, match `--export` baseline.

- **Matcher claim is partially wrong**: Plan §1a Research Insights says `matcher` is "silently ignored" for all 5 new event types. Per Claude Code docs, `SubagentStart`/`SubagentStop` matchers filter on `agent_type`, `SessionEnd` matcher filters on `reason`. Only `UserPromptSubmit` truly has no matcher support. Removing `matcher` from SubagentStart/SubagentStop/SessionEnd loses useful filtering capability.
  - _Impact_: Settings.json configuration is less precise than possible
  - _Recommendation_: Only remove `matcher` from `UserPromptSubmit`. Keep `matcher: "*"` for events where it has meaning (or remove it everywhere since `"*"` is the default).

- **`_redact_tool_input()` crash on whitespace command**: `inp["command"].split()[0]` raises `IndexError` if the command is a non-empty whitespace-only string (e.g., `"  "`). One malformed row crashes the entire span export.
  - _Impact_: Span export fails on edge case data
  - _Recommendation_: `(inp["command"].split() or [""])[0]`
  - _File_: Plan §1d `_redact_tool_input()`

- **`_ts_to_nanos()` no per-row error isolation**: If one DB row has a corrupt timestamp, the unhandled `ValueError` kills the entire export. No indication which row caused it.
  - _Impact_: One bad row prevents all span export
  - _Recommendation_: Wrap in try/except per row, skip bad rows with a warning to stderr

- **`_has_session_column()` called twice in improved `spans_raw()`**: Research Insights §1g calls PRAGMA `table_info` twice per invocation. Result is stable within a call — cache in a local variable.
  - _Impact_: Minor performance (2 extra queries per call)
  - _Recommendation_: `has_session = self._has_session_column()` at top, reuse

### Simplification Opportunities

- **Half-OTLP format** → **Clean custom `claude.hooks.spans/v1` format**: Drop the OTLP-like nesting (`resource_spans`/`scope_spans`). Use a flat, human-readable structure that Claude can parse and humans can grep. Defer actual OTLP wire format to Phase 5 where the `opentelemetry-sdk` dependency is already planned. Current plan creates a format that looks like OTLP but is rejected by every collector — worst of both worlds.

- **Separate `event-logger.sh`** → **Extend `audit-logger.sh`**: Add optional `$1` event type arg. When present, prepend to `tool` column (`PostToolUseFailure:Write`). Eliminates script duplication, reduces deploy surface, centralizes security fixes.

- **Remove `EVENT_LOG_STEPS` constant**: Plan §1e Research Insights already flagged this as YAGNI. `CLAUDE_EVENTS - {"PostToolUse"}` derives the same set. No Phase 1 code uses it.

- **Remove `--span-hours` flag**: Plan §1f Research Insights already flagged as YAGNI. Default 24h covers the common case. Remove from CLI surface.

- **Remove `events: list` from `Span` dataclass**: No code populates it. Pure YAGNI (already noted in plan's own research).

- **Remove `parent_span_id` from `Span` dataclass**: Always `""` in Phase 1. Handle OTLP conditional-omit in serialization, not the data model.

- **Use `f"{prefix_byte:02x}{row_id:014x}"` for span IDs**: Row IDs are unique per DB. SHA256 adds cryptographic overhead for no security benefit. Hex-encoded row IDs are human-readable and sort-stable.

## Plan Revisions Required

_These revisions should be applied to the plan before implementation:_

1. **All sections (1a–1g)**: Merge Research Insights mitigations into canonical code blocks. Move research notes to footnotes or "Rationale" subsections. The implementable code must be the safe version.

2. **§1a**: Either extend `audit-logger.sh` (recommended) or ensure `event-logger.sh` uses temp-file stdin pattern (tee to file, bound what's stored, echo original from file). Add `jq` guard and `head -c` to canonical code.

3. **§1d `spans_to_dict()`**: Replace OTLP-like structure with clean custom format:
   ```python
   {"schema": "claude.hooks.spans/v1", "spans": [{flat span dict}, ...]}
   ```
   Defer camelCase + typed attributes to Phase 5 `to_otlp_wire()`.

4. **§1e**: Change `SKIP_HOOKS_PATTERN` to `re.compile(r"^(fake-fail|ok-step|echo|test-hook|main|event-log)$")` — preserving compiled type.

5. **§1e**: Remove `EVENT_LOG_STEPS`. Keep `CLAUDE_EVENTS` and `GIT_HOOKS` (used in Phase 2-3).

6. **§1f**: Remove `--span-hours` flag. Remove `--no-redact`, replace with `--include-sensitive`.

7. **§1g `spans_raw()`**: Use PRAGMA check (from research) as canonical implementation. Cache `_has_session_column()` result. Add `LIMIT` parameter.

8. **§Deploy**: Reorder — run schema migration before deploying updated scripts. Since `hook-metrics.sh` already calls `_init_hooks_db` at startup, adding the session migration to `_init_hooks_db` makes deployment order-independent.

9. **§Verification**: Update test commands to remove `--span-hours` and `--no-redact` references. Update to use `--include-sensitive`.

## Decisions Confirmed

- [x] **OTLP format strategy**: Clean custom `claude.hooks.spans/v1` format for Phase 1. Flat, human-readable spans array. Defer full OTLP wire format (camelCase, typed attribute wrappers) to Phase 5 where `opentelemetry-sdk` is planned. No half-measures — the current plan's pseudo-OTLP structure (`resource_spans`/`scope_spans`) should be replaced with a simple `{"schema": "...", "spans": [...]}` structure.
- [x] **Extend `audit-logger.sh`** instead of creating separate `event-logger.sh`. Add optional `$1` event type arg; when present, prepend to `tool` column. One script, one deploy, one set of security fixes. Eliminates `event-logger.sh`, `EVENT_LOG_STEPS`, and the settings.json `command` field changes — all 5 new event hooks point to `audit-logger.sh EVENT_TYPE` instead.
- [x] **Privacy tiers**: `--export-spans` default mode matches `--export` baseline. Hash `host.name`, use repo basename (strip user path), no raw commands. `--include-sensitive` unlocks full data. Consistent guarantees across both export modes.

## Dependencies Affected

| Component | Impact | Action Needed |
|---|---|---|
| `config.py:15` | `SKIP_HOOKS_PATTERN` type change if plan followed as-is | Keep `re.compile()`, just extend pattern |
| `db.py:182-192` | `_query()`/`_query_one()` params addition | Backward-compatible, safe to change |
| `db.py:160-177` | `_connect()` CREATE TABLE needs `session` column | Add column to schema for new DBs |
| `db-init.sh:8-43` | `_init_hooks_db()` needs session migration | Follow existing `repo` migration pattern |
| `hook-metrics.sh:51-69` | INSERT needs `session` column + heredoc sanitization | Migration must run first; add `tr -d` to all interpolated values |
| `audit-logger.sh` | Extend for event types (if decision is to extend) | Add optional `$1` for event type prefix |
| `~/.claude/settings.json` | 5 new event hook entries | Document in CLAUDE.md deploy section |

## Test Implications

- Tests expected to fail: None (no existing test suite found in repo)
- Tests needing updates: None (no existing test suite)
- New coverage needed: Unit tests for `spans.py` (span conversion, redaction, timestamp parsing edge cases), integration test for `spans_raw()` graceful degradation on old DB schema
