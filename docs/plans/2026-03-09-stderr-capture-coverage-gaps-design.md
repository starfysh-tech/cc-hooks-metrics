# Design: Stderr Capture + Coverage Gap Detection

**Date:** 2026-03-09
**Status:** Approved
**Scope:** Moderate — fix data capture gaps, add failure attribution, add coverage gap detection

## Problem

The static report shows *that* hooks fail but not *why*. `audit-logger` regressed +700% (exit 5,
54 times in 7d) and cannot be diagnosed from the DB — no stderr is captured. `stop-checks` shows
141 (SIGPIPE) misreported as a near-timeout. Coverage gaps (expected steps with zero recent runs)
are invisible. The goal is workflow validation across all codebases, so aggregated failure reasons
are more useful than per-incident details.

## Approach

Approach A: enrich `hook-metrics.sh` to capture stderr on non-zero exit, store in a new
`stderr_snippet` column, derive exit code semantics at read time, and add Python-only coverage
gap detection. No new tables. No changes to the report's outer structure.

## Data Layer

### `hook-metrics.sh`

On non-zero exit from the wrapped script, capture the first 200 chars of stderr into a variable
and pass it to the `_db_exec` insert. On exit 0, write empty string (no overhead for happy path).

```bash
# Capture stderr of wrapped script to temp file alongside existing /usr/bin/time capture
# On non-zero exit: STDERR_SNIPPET=$(head -c 200 "$STDERR_TMP" | tr '\n' ' ')
# On exit 0: STDERR_SNIPPET=""
```

### Schema migration (`db-init.sh`)

Idempotent `ALTER TABLE` — same pattern as existing migrations:

```sql
ALTER TABLE hook_metrics ADD COLUMN stderr_snippet TEXT DEFAULT '';
```

Safe to run on existing DB; all existing rows get `''`.

### Exit code attribution (`db.py`, read-time only)

Derived from `exit_code` + `stderr_snippet`, not stored:

| exit_code | attribution |
|-----------|-------------|
| 127 | binary not found |
| 124 | timeout |
| 141 | SIGPIPE (broken pipe) |
| 2 (guardrail steps) | guardrail block |
| other non-zero | script error |

## Coverage Gap Detection

### Config (`config.py`)

```python
EXPECTED_STEPS = set(STEP_TIMEOUTS.keys())  # single source of truth, no second list
```

Steps matching `SKIP_HOOKS_PATTERN` excluded from gap checking.

### Query (`db.py`)

New `coverage_gaps(days=7)` method — Python set difference:

```python
seen = {row[0] for row in db.query("SELECT DISTINCT step FROM hook_metrics WHERE ts > ?")}
return EXPECTED_STEPS - seen - skip_steps
```

### Report surface

- Static `[MISSING]` trend lines alongside `[REGR]`/`[SLOW]`
- `Broken Hooks` traffic light extended to flag expected-but-absent steps
- Action item: `⚠️ phi-check — no runs in 7d (expected)`

## Reporting Changes

### Aggregated failure reasons (not most-recent)

Primary query per step:

```sql
SELECT stderr_snippet, COUNT(*) as cnt
FROM hook_metrics
WHERE step = ? AND exit_code != 0 AND ts > ?
GROUP BY stderr_snippet
ORDER BY cnt DESC LIMIT 1
```

Surfaces the most frequent failure pattern across all sessions and repos — appropriate for
workflow analysis, not incident debugging.

### Static report — Action Items

```
❌ audit-logger — 56 failures (+700%) [most common: exit 5 "..." ×41]
   → Investigate: consistent subcommand failure (set -e)
```

### Static report — `[REGR]` trend lines

```
[REGR]  audit-logger  56 fail (was 7, +700%)  top error: exit 5 "..." ×41
```

### TUI — `StepDrillScreen`

New "Top Failure Reasons" panel: top 5 `stderr_snippet` values by count + frequency.
Replaces any "recent failures" feed concept.

### `--export` spans (`spans.py`)

`stderr_snippet` added as `hook.stderr_snippet` attribute on non-zero exit spans.
Empty string spans omit the attribute.

## Validated Findings (from DB inspection)

- `audit-logger` exit 5 = `set -euo pipefail` catching subcommand failure (likely
  `_maybe_prune_hooks_db` or jq edge case). Undiagnosable without this change.
- `stop-checks` exit 141 = SIGPIPE, not a timeout. Misattributed today.
- Aggregation query structure validated; column unavailable until migration runs.

## Out of Scope

- Alerting / push notifications on threshold crossings
- New tables or separate failure event store
- Changes to `--export` JSON schema version
