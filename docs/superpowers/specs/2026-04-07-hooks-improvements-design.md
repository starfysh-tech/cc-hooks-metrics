# Leverage Claude Code Hooks Improvements (up to 2.1.85)

## Problem

Claude Code has added hook capabilities since v2.1.50 that cc-hooks-metrics doesn't use:
- 8 new event types (WorktreeCreate/Remove, TaskCreated/Completed, StopFailure, PermissionDenied, CwdChanged, FileChanged)
- Conditional `if` field for narrowing hook invocation
- Permission decision semantics (allow/deny/ask/defer)
- No version gating — hooks silently fail on old CC versions

## Outcome

- Version check prevents install on unsupported CC versions and warns in reports
- Guardrail hooks fire only when relevant (conditional `if` filtering)
- New events captured and surfaced in reports
- Permission decision and API error analytics available

## Delivery

4 incremental PRs, each independently shippable.

---

## Layer 1: Version Check

### Changes

**`hooks_report/config.py`**
```python
MIN_CC_VERSION = "2.1.50"       # Minimum: worktree hooks
RECOMMENDED_CC_VERSION = "2.1.89"  # Full feature set (incl. PermissionDenied)

def parse_cc_version(version_string: str) -> tuple[int, ...] | None:
    """Parse 'Claude Code v2.1.85' → (2, 1, 85). Returns None on failure."""
    ...
```

**`install.sh`** — new preflight step after Python check:
```bash
# Phase 0.5: Claude Code version
if ! cc_version=$(claude --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1) || [[ -z "$cc_version" ]]; then
    warn "Could not detect Claude Code version — skipping check"
elif version_lt "$cc_version" "$MIN_CC_VERSION"; then
    die "Claude Code $cc_version is below minimum $MIN_CC_VERSION"
elif version_lt "$cc_version" "$RECOMMENDED_CC_VERSION"; then
    warn "Claude Code $cc_version — some features require $RECOMMENDED_CC_VERSION+"
fi
```

**`hooks-report.sh`** — cached version check before Python exec:
```bash
# Cache version for 24h to avoid 200-500ms Node.js startup on every report run
CACHE="$HOME/.claude/hooks/.cc-version-cache"
if [[ ! -f "$CACHE" ]] || [[ $(find "$CACHE" -mmin +1440 2>/dev/null) ]]; then
    claude --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 > "$CACHE" 2>/dev/null
fi
cc_version=$(cat "$CACHE" 2>/dev/null)
# Warning only, don't abort report
```

### Verification
- Mock `claude --version` below MIN → install aborts
- Mock below RECOMMENDED → install warns, report shows banner
- Mock at/above RECOMMENDED → no warnings
- Missing `claude` binary → graceful skip

---

## Layer 2: Settings Optimization

### Conditional `if` narrowing

| Hook | Current | New |
|---|---|---|
| guard-security | PreToolUse (all matchers) | `"if": "Bash\|Read\|Write\|Edit"` (must keep file tools for `.env` protection) |
| guard-python-lint | PostToolUse `Edit\|Write` | `"if": "Edit(*.py)\|Write(*.py)"` |
| guard-python-typecheck | PostToolUse `Edit\|Write` | `"if": "Edit(*.py)\|Write(*.py)"` |
| guard-ts-typecheck | PostToolUse `Edit\|Write` | `"if": "Edit(*.ts)\|Write(*.tsx)\|Edit(*.tsx)\|Write(*.ts)"` |

**Note:** Exact `if` syntax to be verified against CC docs during implementation.

### New event wiring

8 new events in `settings-example.json`. Each gets a **distinct step name** (not collapsed to `event-log`) so Layer 4 queries can filter by step:

```
hook-metrics.sh WorktreeCreate:worktree-create audit-logger.sh
hook-metrics.sh WorktreeRemove:worktree-remove audit-logger.sh
hook-metrics.sh TaskCreated:task-created audit-logger.sh
hook-metrics.sh TaskCompleted:task-completed audit-logger.sh
hook-metrics.sh StopFailure:stop-failure audit-logger.sh
hook-metrics.sh PermissionDenied:permission-denied audit-logger.sh
hook-metrics.sh CwdChanged:cwd-changed audit-logger.sh
hook-metrics.sh FileChanged:file-changed audit-logger.sh
```

All with 5s timeout. Add these step names to `SKIP_HOOKS_PATTERN` so they're excluded from coverage gap detection (they're lifecycle events, not guardrails).

### Verification
- JSON validates
- Trigger events → rows appear in hooks.db

---

## Layer 3: Data Collection

### audit-logger.sh extraction

Extend the jq fallback chain for new event types:

| Event | Extraction | Example tool column value |
|---|---|---|
| TaskCreated | `.subject` or `"task"` | `TaskCreated:Fix auth bug` |
| TaskCompleted | `.subject` or `"task"` | `TaskCompleted:Fix auth bug` |
| StopFailure | `.error_type` or `.reason` | `StopFailure:rate_limit` |
| PermissionDenied | `.tool_name` + `.decision` | `PermissionDenied:Bash:deny` |
| WorktreeCreate | basename of `.path` | `WorktreeCreate:feature-branch` |
| WorktreeRemove | basename of `.path` | `WorktreeRemove:feature-branch` |
| CwdChanged | basename of `.path` | `CwdChanged:src` |
| FileChanged | basename of `.path` | `FileChanged:.env` |

**Payload shapes are approximate** — will inspect actual stdin JSON during implementation and adjust.

**Note on `PermissionDenied` vs `PermissionRequest`:** `PermissionDenied` is a distinct CC event (added ~v2.1.89) that fires when auto-mode classifier denies a tool call. `PermissionRequest` fires when the user is prompted for approval. These are separate events with different payloads — both are tracked but reported differently (PermissionDenied = automated denial analytics, PermissionRequest = user interaction tracking via existing `guard-auto-allow`).

### config.py additions

```python
TASK_EVENTS = {"TaskCreated", "TaskCompleted"}
ERROR_EVENTS = {"StopFailure"}
PERMISSION_DECISIONS = {"allow", "deny", "ask", "defer"}
```

New step timeouts for all event-log entries (5s each).

### Verification
- Trigger each new event → inspect audit_events rows for correct extraction
- Verify hook_metrics rows have correct event/step values

---

## Layer 4: Reporting + Analytics

### New db.py queries

```python
def permission_decisions(self, days: int = 7) -> dict[str, int]:
    """Distribution of allow/deny/ask/defer from PermissionDenied audit events."""

def task_lifecycle(self, days: int = 7) -> TaskLifecycleSummary:
    """Created/completed counts, completion rate.
    Time-to-completion: match by session + subject (best-effort, not guaranteed unique).
    If matching is unreliable, report only counts — skip avg time."""

def api_errors(self, days: int = 7) -> list[ApiErrorSummary]:
    """StopFailure counts by error type."""

def worktree_overhead(self, days: int = 7) -> WorktreeStats:
    """Create/remove timing stats."""
```

### New static.py sections

**Default mode:**
1. Permission Decision Distribution — compact table (only if data exists)
2. API Error Trends — traffic light (green=0, yellow=<5/wk, red=≥5/wk)

**Verbose mode (additional):**
3. Task Lifecycle — created vs completed, completion rate, avg time
4. Worktree Overhead — create/remove timing stats

### Traffic light grid

Add 6th light: "API Reliability" based on StopFailure frequency. This requires:
- Extending `ReliabilitySummary` dataclass with `stop_failure_count` and `stop_failure_types` fields
- Extending `assess()` query to count StopFailure events
- Updating `traffic_light_grid()` layout from 5-cell (2+2+1) to 6-cell (2+2+2)

### New advisor suggestions

| Category | Condition | Suggestion |
|---|---|---|
| permission-noise | >50% PermissionDenied on same tool | "Add auto-allow rule for {tool}" |
| api-reliability | StopFailure rate above threshold | "Check API quota/billing" |

**Removed:** `conditional-optimization` suggestion — no data source exists to detect guardrails firing on non-matching files. Could be added later if guardrail scripts report match/no-match status.

### Verification
- `hooks-report.sh` — new sections appear when data exists, hidden when empty
- `hooks-report.sh --verbose` — task lifecycle + worktree sections appear
- `hooks-report.sh --export` — new metrics in JSON output
- All Rich output uses `Text` objects, not markup strings
