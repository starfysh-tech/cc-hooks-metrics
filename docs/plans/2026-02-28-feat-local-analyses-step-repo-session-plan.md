---
title: "Phase 2: Local Analyses — Step, Repo, and Session"
phase: 2
date: 2026-02-28
dependencies: "Phase 1 (session queries only; step/repo queries are independent)"
status: ready
---

# Phase 2: Local Analyses — Step, Repo, and Session

## Overview

Phase 2 adds per-step reliability metrics (p50/p90/p99, pain index), per-repo health profiles, and per-session summaries to `hooks_report/db.py`. These queries power the Phase 3 TUI screens and static sections. Step and repo queries work against existing data; session queries require the Phase 1 `session` column and are gated behind a runtime PRAGMA check.

## Dependencies

- **Independent of Phase 1**: `step_reliability()`, `step_drilldown()`, `repo_profiles()`, `under_instrumented_repos()` — all use existing `hook_metrics` columns
- **Requires Phase 1**: `session_list()`, `session_timeline()` — need the `session` column added in Phase 1b
- **Gating mechanism**: `_has_session_column()` cached PRAGMA check prevents runtime errors on pre-migration databases

## Implementation

### 2a. New dataclasses in `hooks_report/db.py`

Add after the existing `HealthSummary` dataclass (line 119) and before the `# -- Helpers` section (line 121).

**`StepReliability`** — per-step reliability with percentile latencies and pain index:

```python
@dataclass
class StepReliability:
    step: str
    total_runs: int
    failures: int
    fail_rate: Optional[float]    # NULL-preserving (None if 0 runs)
    p50_ms: int
    p90_ms: int
    p99_ms: int
    avg_ms: float
    max_ms: int
    total_s: float                # total duration in seconds (for pain calc)
    pain_index: float             # total_s * (fail_rate / 100); 0 if fail_rate is None
```

**`RepoProfile`** — per-repo health dashboard:

```python
@dataclass
class RepoProfile:
    repo: str                     # display name (stripped of /Users/.../Code/ prefix)
    total_runs: int
    failures: int
    fail_rate: Optional[float]
    distinct_steps: int           # number of unique hook steps seen
    overhead_ms: int              # total duration_ms
    overhead_min: float           # overhead_ms / 60000
    session_count: int            # 0 if session column missing (approximate)
    guardrail_density: float      # distinct_steps / session_count (0 if no sessions)
```

**`SessionSummary`** — per-session overview (Phase 1 dependent):

```python
@dataclass
class SessionSummary:
    session_id: str
    first_ts: str                 # earliest timestamp in session
    last_ts: str                  # latest timestamp
    duration_s: int               # (last - first) in seconds
    hook_runs: int                # count from hook_metrics
    hook_failures: int
    tool_uses: int                # count from audit_events
    overhead_ms: int              # sum(duration_ms) from hook_metrics
    distinct_steps: int
```

**`SessionTimeline`** — single event in a session timeline (Phase 1 dependent):

```python
@dataclass
class SessionTimeline:
    ts: str
    source: str                   # "hook" | "tool"
    name: str                     # step name or tool name
    duration_ms: int              # 0 for tool events
    exit_code: Optional[int]      # None for tool events
    detail: str                   # cmd for hooks, truncated input for tools
```

### 2b. Session column gating helper

Add to the `HooksDB` class, after the `close()` method (line 187):

```python
def _has_session_column(self) -> bool:
    """Check if hook_metrics has a session column (Phase 1 migration).

    Result is cached on the instance to avoid repeated PRAGMA queries.
    """
    if hasattr(self, "_session_col_cached"):
        return self._session_col_cached
    rows = self._query("PRAGMA table_info(hook_metrics)")
    col_names = {r[1] for r in rows}
    self._session_col_cached = "session" in col_names
    return self._session_col_cached
```

Key notes:
- Uses `PRAGMA table_info(hook_metrics)` which returns `(cid, name, type, notnull, dflt_value, pk)` tuples
- Column name is at index 1
- Cached via `_session_col_cached` instance attribute (not class-level — each HooksDB instance checks once)

### 2c. Query methods — independent of Phase 1

Add these methods to `HooksDB` after the existing `health_24h()` method (line 632).

#### `step_reliability(days=7, repo=None) -> list[StepReliability]`

Per-step p50/p90/p99 via window functions, pain index = `total_s * (fail_rate / 100)`.

```python
def step_reliability(self, days: int = 7, repo: str | None = None) -> list[StepReliability]:
    sem = _semantic_exit_placeholders()
    repo_filter = "AND repo = ?" if repo else ""
    params = [repo] if repo else []

    rows = self._query(f"""
    WITH ranked AS (
      SELECT step, duration_ms, exit_code,
        ROW_NUMBER() OVER (PARTITION BY step ORDER BY duration_ms) AS rn,
        COUNT(*) OVER (PARTITION BY step) AS cnt
      FROM hook_metrics
      WHERE ts > datetime('now', '-{days} days')
        AND duration_ms > 0
        {repo_filter}
    )""", params)
    SELECT step,
      MAX(cnt) AS total_runs,
      SUM(CASE WHEN exit_code != 0 AND step NOT IN ({sem}) THEN 1 ELSE 0 END) AS failures,
      ROUND(100.0 * SUM(CASE WHEN exit_code != 0 AND step NOT IN ({sem}) THEN 1 ELSE 0 END)
            / NULLIF(MAX(cnt), 0), 1) AS fail_rate,
      MAX(CASE WHEN rn = CAST(CEIL(0.50 * cnt) AS INTEGER) THEN duration_ms END) AS p50_ms,
      MAX(CASE WHEN rn = CAST(CEIL(0.90 * cnt) AS INTEGER) THEN duration_ms END) AS p90_ms,
      MAX(CASE WHEN rn = CAST(CEIL(0.99 * cnt) AS INTEGER) THEN duration_ms END) AS p99_ms,
      ROUND(AVG(duration_ms), 1) AS avg_ms,
      MAX(duration_ms) AS max_ms,
      ROUND(SUM(duration_ms) / 1000.0, 2) AS total_s
    FROM ranked
    GROUP BY step
    ORDER BY total_s DESC
    """)

    result: list[StepReliability] = []
    for row in rows:
        step, total_runs, failures, fail_rate, p50, p90, p99, avg_ms, max_ms, total_s = row
        fr = _opt_float(fail_rate)
        ts = float(total_s or 0)
        pain = ts * (fr / 100) if fr else 0.0
        result.append(StepReliability(
            step=step,
            total_runs=_int(total_runs),
            failures=_int(failures),
            fail_rate=fr,
            p50_ms=_int(p50),
            p90_ms=_int(p90),
            p99_ms=_int(p99),
            avg_ms=float(avg_ms or 0),
            max_ms=_int(max_ms),
            total_s=ts,
            pain_index=round(pain, 2),
        ))
    return result
```

Notes:
- Reuses the `ROW_NUMBER() OVER` pattern from existing `perf_compact()` (line 309) and `perf_full()` (line 688)
- Pain index calculated in Python (not SQL) because it depends on the NULL-safe `fail_rate`
- `repo` filter is optional — used by Phase 3 `StepDrillScreen` for per-repo breakdown
- Semantic exit steps excluded from failure counts (consistent with `assess()`)

#### `step_drilldown(step, days=7) -> dict`

Per-repo breakdown, per-day trend, and exit code distribution for a single step.

```python
def step_drilldown(self, step: str, days: int = 7) -> dict:
    sem = _semantic_exit_placeholders()

    # Per-repo breakdown
    repo_rows = self._query(f"""
    SELECT
      COALESCE(NULLIF(repo, ''), '(global/unknown)') AS project,
      COUNT(*) AS runs,
      SUM(CASE WHEN exit_code != 0 AND step NOT IN ({sem}) THEN 1 ELSE 0 END) AS failures,
      ROUND(AVG(duration_ms), 1) AS avg_ms,
      MAX(duration_ms) AS max_ms
    FROM hook_metrics
    WHERE step = ? AND ts > datetime('now', '-{days} days')
    GROUP BY repo ORDER BY runs DESC
    """, [step])

    # Per-day trend (last N days)
    daily_rows = self._query(f"""
    SELECT DATE(ts) AS day,
      COUNT(*) AS runs,
      SUM(CASE WHEN exit_code != 0 AND step NOT IN ({sem}) THEN 1 ELSE 0 END) AS failures,
      ROUND(AVG(duration_ms), 1) AS avg_ms
    FROM hook_metrics
    WHERE step = ? AND ts > datetime('now', '-{days} days')
    GROUP BY DATE(ts) ORDER BY day
    """)

    # Exit code distribution
    exit_rows = self._query(f"""
    SELECT exit_code, COUNT(*) AS cnt
    FROM hook_metrics
    WHERE step = ? AND ts > datetime('now', '-{days} days')
    GROUP BY exit_code ORDER BY cnt DESC
    """, [step])

    return {
        "step": step,
        "by_repo": [
            {"repo": r[0], "runs": _int(r[1]), "failures": _int(r[2]),
             "avg_ms": float(r[3] or 0), "max_ms": _int(r[4])}
            for r in repo_rows
        ],
        "daily": [
            {"day": r[0], "runs": _int(r[1]), "failures": _int(r[2]),
             "avg_ms": float(r[3] or 0)}
            for r in daily_rows
        ],
        "exit_codes": [
            {"code": _int(r[0]), "count": _int(r[1])}
            for r in exit_rows
        ],
    }
```

Notes:
- Returns a dict (not a dataclass) since the structure varies per section
- Repo display name follows existing `projects_compact()` pattern (line 474) but without user-specific path stripping — that's a display concern for render.py
- Used by `--step NAME` CLI arg and `StepDrillScreen` TUI screen

#### `repo_profiles(days=7) -> list[RepoProfile]`

Per-repo health dashboard.

```python
def repo_profiles(self, days: int = 7) -> list[RepoProfile]:
    import getpass
    user = getpass.getuser()
    sem = _semantic_exit_placeholders()

    has_session = self._has_session_column()
    session_select = (
        "COUNT(DISTINCT CASE WHEN session != '' THEN session END)"
        if has_session else "0"
    )

    rows = self._query(f"""
    SELECT
      COALESCE(NULLIF(REPLACE(repo, '/Users/{user}/Code/', ''), ''), '(global/unknown)') AS project,
      COUNT(*) AS total_runs,
      SUM(CASE WHEN exit_code != 0 AND step NOT IN ({sem}) THEN 1 ELSE 0 END) AS failures,
      ROUND(100.0 * SUM(CASE WHEN exit_code != 0 AND step NOT IN ({sem}) THEN 1 ELSE 0 END)
            / NULLIF(COUNT(*), 0), 1) AS fail_rate,
      COUNT(DISTINCT step) AS distinct_steps,
      SUM(duration_ms) AS overhead_ms,
      {session_select} AS session_count
    FROM hook_metrics
    WHERE ts > datetime('now', '-{days} days')
    GROUP BY repo ORDER BY overhead_ms DESC
    """)

    result: list[RepoProfile] = []
    for row in rows:
        project, total_runs, failures, fail_rate, steps, overhead_ms, sessions = row
        oh = _int(overhead_ms)
        sc = _int(sessions)
        result.append(RepoProfile(
            repo=project,
            total_runs=_int(total_runs),
            failures=_int(failures),
            fail_rate=_opt_float(fail_rate),
            distinct_steps=_int(steps),
            overhead_ms=oh,
            overhead_min=round(oh / 60000, 1),
            session_count=sc,
            guardrail_density=round(_int(steps) / sc, 2) if sc > 0 else 0.0,
        ))
    return result
```

Notes:
- Session count is conditional on `_has_session_column()` — returns 0 if column missing
- `guardrail_density` = distinct steps / sessions (how many unique hooks fire per session)
- Reuses existing `getpass.getuser()` pattern from `projects_compact()` (line 474)

#### `under_instrumented_repos(days=7) -> list[tuple[str, int]]`

Repos with fewer than `MIN_STEPS_FOR_COVERAGE` distinct steps.

```python
def under_instrumented_repos(self, days: int = 7) -> list[tuple[str, int]]:
    import getpass
    user = getpass.getuser()

    rows = self._query(f"""
    SELECT
      COALESCE(NULLIF(REPLACE(repo, '/Users/{user}/Code/', ''), ''), '(global/unknown)') AS project,
      COUNT(DISTINCT step) AS distinct_steps
    FROM hook_metrics
    WHERE ts > datetime('now', '-{days} days')
    GROUP BY repo
    HAVING distinct_steps < {config.MIN_STEPS_FOR_COVERAGE}
      AND COUNT(*) >= {config.MIN_RUNS_FOR_TREND}
    ORDER BY distinct_steps ASC
    """)

    return [(r[0], _int(r[1])) for r in rows]
```

Notes:
- Only flags repos with enough runs to be meaningful (`MIN_RUNS_FOR_TREND`)
- Returns `(repo_display_name, step_count)` tuples

### 2d. Query methods — Phase 1 dependent (session column required)

#### `session_list(days=7, limit=20) -> list[SessionSummary]`

Top sessions by failure count, joining `hook_metrics` and `audit_events`.

```python
def session_list(self, days: int = 7, limit: int = 20) -> list[SessionSummary]:
    if not self._has_session_column():
        return []

    sem = _semantic_exit_placeholders()

    rows = self._query(f"""
    SELECT
      h.session,
      MIN(h.ts) AS first_ts,
      MAX(h.ts) AS last_ts,
      CAST((julianday(MAX(h.ts)) - julianday(MIN(h.ts))) * 86400 AS INTEGER) AS duration_s,
      COUNT(*) AS hook_runs,
      SUM(CASE WHEN h.exit_code != 0 AND h.step NOT IN ({sem}) THEN 1 ELSE 0 END) AS hook_failures,
      COALESCE(a.tool_uses, 0) AS tool_uses,
      SUM(h.duration_ms) AS overhead_ms,
      COUNT(DISTINCT h.step) AS distinct_steps
    FROM hook_metrics h
    LEFT JOIN (
      SELECT session, COUNT(*) AS tool_uses
      FROM audit_events
      WHERE ts > datetime('now', '-{days} days')
      GROUP BY session
    ) a ON h.session = a.session
    WHERE h.ts > datetime('now', '-{days} days')
      AND h.session != ''
    GROUP BY h.session
    ORDER BY hook_failures DESC, overhead_ms DESC
    LIMIT {limit}
    """)

    return [
        SessionSummary(
            session_id=r[0],
            first_ts=r[1],
            last_ts=r[2],
            duration_s=_int(r[3]),
            hook_runs=_int(r[4]),
            hook_failures=_int(r[5]),
            tool_uses=_int(r[6]),
            overhead_ms=_int(r[7]),
            distinct_steps=_int(r[8]),
        )
        for r in rows
    ]
```

Notes:
- Returns empty list if session column missing (graceful degradation)
- LEFT JOIN to `audit_events` on session — some sessions may have hooks but no audit data
- Sorted by failures first (worst sessions at top), then by overhead
- `julianday()` difference * 86400 gives seconds between first and last event

#### `session_timeline(session_id) -> list[SessionTimeline]`

Interleaved timeline of hooks + tools within a single session.

```python
def session_timeline(self, session_id: str) -> list[SessionTimeline]:
    if not self._has_session_column():
        return []

    rows = self._query("""
    SELECT ts, 'hook' AS source, step AS name, duration_ms, exit_code,
      COALESCE(NULLIF(TRIM(cmd), ''), '(unknown)') AS detail
    FROM hook_metrics
    WHERE session = ?
    UNION ALL
    SELECT ts, 'tool' AS source, tool AS name, 0 AS duration_ms, NULL AS exit_code,
      SUBSTR(input, 1, 120) AS detail
    FROM audit_events
    WHERE session = ?
    ORDER BY ts
    """, [session_id, session_id])

    return [
        SessionTimeline(
            ts=r[0],
            source=r[1],
            name=r[2],
            duration_ms=_int(r[3]),
            exit_code=_int(r[4]) if r[4] is not None else None,
            detail=r[5] or "",
        )
        for r in rows
    ]
```

Notes:
- `UNION ALL` interleaves both tables, sorted by timestamp
- Tool input truncated to 120 chars in SQL (not a privacy concern — audit_events already has full input)
- `exit_code` is `None` for tool events (no exit concept)

### 2e. Config additions in `hooks_report/config.py`

Add after `TIMEOUT_RED_PCT` (line 25), before `DEFAULT_DB_PATH`:

```python
PAIN_INDEX_RED = 10.0          # pain index threshold for red status
PAIN_INDEX_YELLOW = 3.0        # pain index threshold for yellow status
MIN_STEPS_FOR_COVERAGE = 3     # repos with fewer distinct steps are under-instrumented
```

These thresholds are initial guesses based on the existing data distribution. The pain index formula `total_s * (fail_rate / 100)` means:
- A step with 10s total overhead and 100% failure rate has pain_index = 10.0 (red)
- A step with 30s total overhead and 10% failure rate has pain_index = 3.0 (yellow)
- Calibration against live data recommended after first deployment

## Files Changed

| File | Change | Notes |
|------|--------|-------|
| `hooks_report/db.py` | +4 dataclasses, +7 methods, +1 helper | `StepReliability`, `RepoProfile`, `SessionSummary`, `SessionTimeline` dataclasses; `_has_session_column()`, `step_reliability()`, `step_drilldown()`, `repo_profiles()`, `under_instrumented_repos()`, `session_list()`, `session_timeline()` methods |
| `hooks_report/config.py` | +3 constants | `PAIN_INDEX_RED`, `PAIN_INDEX_YELLOW`, `MIN_STEPS_FOR_COVERAGE` |

## Verification

```bash
# Verify step reliability query returns data (uses existing columns)
python3 -c "
from hooks_report.db import HooksDB
db = HooksDB()
for s in db.step_reliability(days=7):
    print(f'{s.step:<25} p50={s.p50_ms:>6}ms  p90={s.p90_ms:>6}ms  pain={s.pain_index:.2f}')
"

# Verify step drilldown
python3 -c "
from hooks_report.db import HooksDB
import json
db = HooksDB()
data = db.step_drilldown('audit-logger', days=7)
print(json.dumps(data, indent=2))
"

# Verify repo profiles
python3 -c "
from hooks_report.db import HooksDB
db = HooksDB()
for r in db.repo_profiles():
    print(f'{r.repo:<35} steps={r.distinct_steps}  fail={r.fail_rate}%  sessions={r.session_count}')
"

# Verify session column gating (should return empty before Phase 1)
python3 -c "
from hooks_report.db import HooksDB
db = HooksDB()
print(f'has_session_column: {db._has_session_column()}')
print(f'session_list: {len(db.session_list())} sessions')
"

# Verify under-instrumented repos
python3 -c "
from hooks_report.db import HooksDB
db = HooksDB()
for repo, steps in db.under_instrumented_repos():
    print(f'{repo}: {steps} steps')
"

# Verify new config constants exist
python3 -c "
from hooks_report import config
print(f'PAIN_INDEX_RED={config.PAIN_INDEX_RED}')
print(f'PAIN_INDEX_YELLOW={config.PAIN_INDEX_YELLOW}')
print(f'MIN_STEPS_FOR_COVERAGE={config.MIN_STEPS_FOR_COVERAGE}')
"
```

## Risks & Notes

- **Pain index calibration**: The formula `total_s * (fail_rate / 100)` and thresholds (`RED=10.0`, `YELLOW=3.0`) are initial estimates. Run `step_reliability()` against live data and inspect the distribution before finalizing thresholds.
- **Percentile accuracy**: Using `ROW_NUMBER()` for percentile calculation is exact for small datasets but approximates for large ones. Given typical hook_metrics volume (~6K rows/week), this is acceptable.
- **SQL injection in `repo` filter**: The `repo` parameter in `step_reliability()` is interpolated directly. This is safe because it's only called programmatically (from TUI/static code), never from user input. If exposed via CLI in the future, use parameterized queries.
- **Session column missing**: All session-dependent queries return empty results (not errors) when the column is absent. The `_has_session_column()` cache means the PRAGMA check runs at most once per `HooksDB` instance.
- **`step_drilldown()` returns dict**: Unlike other methods that return dataclasses, this returns a dict because the three sub-queries have different shapes. A dedicated dataclass would add complexity without benefit since this is consumed by render functions that iterate the sub-lists directly.
- **`getpass.getuser()` in repo_profiles**: Follows the existing pattern in `projects_compact()` (line 474). If the username assumption fails, repos show full paths (functional, just verbose).
