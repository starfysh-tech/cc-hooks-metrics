from __future__ import annotations

import getpass
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from . import config


class HooksDBError(Exception):
    """Raised when a database query fails."""


# ── Dataclasses ──────────────────────────────────────────────────────────────


@dataclass
class ReliabilitySummary:
    # 24h
    rel_total: int
    rel_failures: int
    rel_fail_rate: Optional[float]
    # 7d
    broken_count: int
    broken_steps: int
    # review (7d)
    review_runs: int
    review_findings: int
    # regressions (14d)
    regr_count: int
    # performance timeout assessment (7d)
    worst_pct: int
    n_over: int
    # 24h overhead
    overhead_24h_ms: int
    # 7d totals (for subtitle)
    runs_7d: int
    overhead_7d_ms: int


@dataclass
class StepPerformance:
    step: str
    avg_ms: float
    max_ms: int
    total_n: int
    total_ms: int


@dataclass
class WowSummary:
    cur_runs: int
    prev_runs: int
    cur_fail: int
    prev_fail: int
    cur_rate: Optional[float]
    prev_rate: Optional[float]
    cur_ms: int
    prev_ms: int


@dataclass
class FailureTrend:
    step: str
    cur_f: int
    prev_f: int
    cur_r: int
    prev_r: int


@dataclass
class LatencyRegression:
    step: str
    cur_avg: int
    prev_avg: int
    total_n: int


@dataclass
class CoverageGap:
    step: str
    cur_r: int
    prev_r: int


@dataclass
class ProjectOverhead:
    project: str
    total_min: float
    runs: int
    fail_rate: Optional[float]


@dataclass
class BrokenHook:
    step: str
    cmd: str
    count: int
    last_fail: str
    last_success: Optional[str]


@dataclass
class ActionItem:
    category: str   # TIMEOUT, BROKEN, SLOW, FAIL, MISSING
    severity: str   # red, yellow
    step: str
    detail: str
    fix: str


@dataclass
class FailureReason:
    snippet: str
    count: int
    exit_code: int | None


@dataclass
class HealthSummary:
    total: int
    failures: int
    fail_pct: Optional[float]
    review_findings: int
    review_runs: int
    overhead_ms: int
    max_latency_ms: int
    slow_count: int


@dataclass
class StepReliability:
    step: str
    total_runs: int
    failures: int
    fail_rate: Optional[float]
    p50_ms: int
    p90_ms: int
    p99_ms: int
    avg_ms: float
    max_ms: int
    total_s: float
    pain_index: float


@dataclass
class RepoProfile:
    repo: str
    total_runs: int
    failures: int
    fail_rate: Optional[float]
    distinct_steps: int
    overhead_ms: int
    overhead_min: float
    session_count: int
    guardrail_density: float


@dataclass
class SessionSummary:
    session_id: str
    first_ts: str
    last_ts: str
    duration_s: int
    hook_runs: int
    hook_failures: int
    tool_uses: int
    overhead_ms: int
    distinct_steps: int


@dataclass
class SessionTimeline:
    ts: str
    source: str       # "hook" | "tool"
    name: str
    duration_ms: int
    exit_code: Optional[int]
    detail: str


@dataclass
class HotSequence:
    prev_step: str
    step: str
    total: int
    failures: int
    fail_rate: float


@dataclass
class PeriodAggregate:
    total_runs: int
    failures: int
    fail_rate: float
    overhead_ms: int
    unique_steps: int
    unique_repos: int
    start_ts: str
    end_ts: str


@dataclass
class GuardrailSummary:
    step: str
    total_runs: int
    blocks: int
    block_rate: Optional[float]
    avg_ms: float


# ── Helpers ──────────────────────────────────────────────────────────────────


def _int(val) -> int:
    """Safely coerce a sqlite value to int (handles float strings like '88146.0')."""
    if val is None:
        return 0
    return int(round(float(val)))


def _opt_float(val) -> Optional[float]:
    """Return None for NULL, else float."""
    if val is None:
        return None
    return float(val)


def _semantic_exit_placeholders() -> str:
    """Build SQL IN-list for SEMANTIC_EXIT_STEPS."""
    return ", ".join(f"'{s}'" for s in config.SEMANTIC_EXIT_STEPS)


def _failure_filter(step_col: str = "step") -> str:
    """SQL fragment: true if row counts as a failure.

    Excludes semantic-exit steps (e.g. codex-review where exit=1 means findings,
    not errors) and guardrail blocks (exit=2 from GUARDRAIL_STEPS means the guard
    correctly blocked a dangerous action — not a failure).
    """
    sem = _semantic_exit_placeholders()
    base = f"exit_code != 0 AND {step_col} NOT IN ({sem})"
    if not config.GUARDRAIL_STEPS:
        return base
    guard = ", ".join(f"'{s}'" for s in config.GUARDRAIL_STEPS)
    return f"{base} AND NOT ({step_col} IN ({guard}) AND exit_code = 2)"


# ── Database class ───────────────────────────────────────────────────────────


class HooksDB:
    def __init__(self, path: str | None = None):
        self.path = path or config.DEFAULT_DB_PATH
        self._conn: Optional[sqlite3.Connection] = None

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            import os
            if not os.path.exists(self.path):
                # Init empty schema so queries return zero rows instead of erroring
                conn = sqlite3.connect(self.path)
                conn.executescript("""
                    PRAGMA journal_mode=WAL;
                    CREATE TABLE IF NOT EXISTS hook_metrics (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ts TEXT NOT NULL, hook TEXT NOT NULL, step TEXT NOT NULL,
                        cmd TEXT NOT NULL, exit_code INTEGER NOT NULL,
                        duration_ms INTEGER NOT NULL, real_s REAL NOT NULL,
                        user_s REAL NOT NULL, sys_s REAL NOT NULL,
                        branch TEXT DEFAULT '', sha TEXT DEFAULT '',
                        host TEXT DEFAULT '', repo TEXT DEFAULT '',
                    session TEXT DEFAULT ''
                    );
                    CREATE INDEX IF NOT EXISTS idx_hook_metrics_session
                      ON hook_metrics(session) WHERE session != '';
                    CREATE TABLE IF NOT EXISTS audit_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ts TEXT NOT NULL, session TEXT NOT NULL,
                        tool TEXT NOT NULL, input TEXT NOT NULL
                    );
                """)
                conn.close()
            self._conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        return self._conn

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

    def close(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ── assess() ─────────────────────────────────────────────────────────────

    def assess(self) -> ReliabilitySummary:
        # Query 1: 4-category UNION
        rows = self._query(f"""
SELECT 'reliability',
  COUNT(*),
  SUM(CASE WHEN {_failure_filter()} THEN 1 ELSE 0 END),
  ROUND(100.0 * SUM(CASE WHEN {_failure_filter()} THEN 1 ELSE 0 END)
        / NULLIF(COUNT(*), 0), 1)
FROM hook_metrics WHERE ts > datetime('now', '-1 day')
UNION ALL
SELECT 'broken',
  SUM(CASE WHEN exit_code = 127 THEN 1 ELSE 0 END),
  COUNT(DISTINCT CASE WHEN exit_code = 127 THEN step END),
  NULL
FROM hook_metrics WHERE ts > datetime('now', '-7 days')
UNION ALL
SELECT 'review',
  SUM(CASE WHEN step = 'codex-review' THEN 1 ELSE 0 END),
  SUM(CASE WHEN step = 'codex-review' AND exit_code != 0 THEN 1 ELSE 0 END),
  NULL
FROM hook_metrics WHERE ts > datetime('now', '-7 days')
UNION ALL
SELECT 'regressions', COUNT(*), NULL, NULL
FROM (
  SELECT step,
    AVG(CASE WHEN ts > datetime('now','-7 days') THEN duration_ms END) AS cur_avg,
    AVG(CASE WHEN ts BETWEEN datetime('now','-14 days') AND datetime('now','-7 days') THEN duration_ms END) AS prev_avg,
    SUM(CASE WHEN ts > datetime('now','-7 days') THEN 1 ELSE 0 END) AS cur_runs,
    SUM(CASE WHEN ts BETWEEN datetime('now','-14 days') AND datetime('now','-7 days') THEN 1 ELSE 0 END) AS prev_runs
  FROM hook_metrics WHERE ts > datetime('now','-14 days') AND duration_ms > 0
  GROUP BY step
  HAVING cur_avg IS NOT NULL AND prev_avg IS NOT NULL
    AND cur_avg > prev_avg
    AND CAST(cur_avg - prev_avg AS REAL) / NULLIF(prev_avg, 0) > {config.REGRESSION_PCT_THRESHOLD}
    AND (cur_runs + prev_runs) >= {config.MIN_RUNS_FOR_TREND}
    AND (cur_avg - prev_avg) * cur_runs / 1000.0 >= {config.IMPACT_THRESHOLD_S}
)
""")

        rel_total = 0
        rel_failures = 0
        rel_fail_rate: Optional[float] = None
        broken_count = 0
        broken_steps = 0
        review_runs = 0
        review_findings = 0
        regr_count = 0

        for cat, v1, v2, v3 in rows:
            if cat == "reliability":
                rel_total = _int(v1)
                rel_failures = _int(v2)
                rel_fail_rate = _opt_float(v3)
            elif cat == "broken":
                broken_count = _int(v1)
                broken_steps = _int(v2)
            elif cat == "review":
                review_runs = _int(v1)
                review_findings = _int(v2)
            elif cat == "regressions":
                regr_count = _int(v1)

        # Query 2: Timeout assessment per step
        timeout_rows = self._query("""
SELECT step, MAX(duration_ms) FROM hook_metrics
WHERE ts > datetime('now', '-7 days') AND duration_ms > 0
GROUP BY step
""")

        worst_pct = 0
        n_over = 0
        for step, max_ms in timeout_rows:
            limit = config.STEP_TIMEOUTS.get(step, 0)
            if limit <= 0:
                continue
            pct = int(round(float(max_ms) / limit * 100))
            if pct >= 100:
                n_over += 1
            if pct > worst_pct:
                worst_pct = pct

        # Query 3: 24h overhead
        row = self._query_one(
            "SELECT COALESCE(SUM(duration_ms),0) FROM hook_metrics "
            "WHERE ts > datetime('now','-1 day')"
        )
        # aggregate without GROUP BY always returns exactly one row
        assert row is not None
        overhead_24h_ms = _int(row[0])

        # Query 4: 7d runs and overhead
        row = self._query_one(
            "SELECT COUNT(*), COALESCE(SUM(duration_ms),0) FROM hook_metrics "
            "WHERE ts > datetime('now','-7 days')"
        )
        # aggregate without GROUP BY always returns exactly one row
        assert row is not None
        runs_7d = _int(row[0])
        overhead_7d_ms = _int(row[1])

        return ReliabilitySummary(
            rel_total=rel_total,
            rel_failures=rel_failures,
            rel_fail_rate=rel_fail_rate,
            broken_count=broken_count,
            broken_steps=broken_steps,
            review_runs=review_runs,
            review_findings=review_findings,
            regr_count=regr_count,
            worst_pct=worst_pct,
            n_over=n_over,
            overhead_24h_ms=overhead_24h_ms,
            runs_7d=runs_7d,
            overhead_7d_ms=overhead_7d_ms,
        )

    # ── perf_compact() ───────────────────────────────────────────────────────

    def perf_compact(self) -> list[StepPerformance]:
        rows = self._query("""
WITH ranked AS (
  SELECT step, duration_ms,
    ROW_NUMBER() OVER (PARTITION BY step ORDER BY duration_ms) AS rn,
    COUNT(*) OVER (PARTITION BY step) AS cnt
  FROM hook_metrics WHERE ts > datetime('now', '-7 days') AND duration_ms > 0
)
SELECT step,
  ROUND(AVG(duration_ms), 1) AS avg_ms,
  MAX(duration_ms) AS max_ms,
  MAX(cnt) AS total_n,
  SUM(duration_ms) AS total_ms
FROM ranked GROUP BY step ORDER BY total_ms DESC
""")

        result: list[StepPerformance] = []
        for step, avg_ms, max_ms, total_n, total_ms in rows:
            avg_f = float(avg_ms)
            if avg_f < 500 and step not in config.STEP_TIMEOUTS:
                continue
            result.append(StepPerformance(
                step=step,
                avg_ms=avg_f,
                max_ms=_int(max_ms),
                total_n=_int(total_n),
                total_ms=_int(total_ms),
            ))
            if len(result) >= 12:
                break
        return result

    # ── wow_summary() ────────────────────────────────────────────────────────

    def wow_summary(self) -> WowSummary:
        row = self._query_one(f"""
SELECT
  SUM(CASE WHEN ts > datetime('now','-7 days') THEN 1 ELSE 0 END) AS cur_runs,
  SUM(CASE WHEN ts BETWEEN datetime('now','-14 days') AND datetime('now','-7 days') THEN 1 ELSE 0 END) AS prev_runs,
  SUM(CASE WHEN ts > datetime('now','-7 days') AND {_failure_filter()} THEN 1 ELSE 0 END) AS cur_fail,
  SUM(CASE WHEN ts BETWEEN datetime('now','-14 days') AND datetime('now','-7 days') AND {_failure_filter()} THEN 1 ELSE 0 END) AS prev_fail,
  ROUND(100.0 * SUM(CASE WHEN ts > datetime('now','-7 days') AND {_failure_filter()} THEN 1 ELSE 0 END) / NULLIF(SUM(CASE WHEN ts > datetime('now','-7 days') THEN 1 ELSE 0 END),0),1) AS cur_rate,
  ROUND(100.0 * SUM(CASE WHEN ts BETWEEN datetime('now','-14 days') AND datetime('now','-7 days') AND {_failure_filter()} THEN 1 ELSE 0 END) / NULLIF(SUM(CASE WHEN ts BETWEEN datetime('now','-14 days') AND datetime('now','-7 days') THEN 1 ELSE 0 END),0),1) AS prev_rate,
  SUM(CASE WHEN ts > datetime('now','-7 days') THEN duration_ms ELSE 0 END) AS cur_ms,
  SUM(CASE WHEN ts BETWEEN datetime('now','-14 days') AND datetime('now','-7 days') THEN duration_ms ELSE 0 END) AS prev_ms
FROM hook_metrics
WHERE ts > datetime('now','-14 days')
""")
        # aggregate without GROUP BY always returns exactly one row
        assert row is not None
        return WowSummary(
            cur_runs=_int(row[0]),
            prev_runs=_int(row[1]),
            cur_fail=_int(row[2]),
            prev_fail=_int(row[3]),
            cur_rate=_opt_float(row[4]),
            prev_rate=_opt_float(row[5]),
            cur_ms=_int(row[6]),
            prev_ms=_int(row[7]),
        )

    # ── failure_regressions() ────────────────────────────────────────────────

    def failure_regressions(self) -> list[FailureTrend]:
        sem = _semantic_exit_placeholders()
        rows = self._query(f"""
SELECT step,
  SUM(CASE WHEN ts > datetime('now','-7 days') AND {_failure_filter()} THEN 1 ELSE 0 END) AS cur_f,
  SUM(CASE WHEN ts BETWEEN datetime('now','-14 days') AND datetime('now','-7 days') AND {_failure_filter()} THEN 1 ELSE 0 END) AS prev_f,
  SUM(CASE WHEN ts > datetime('now','-7 days') THEN 1 ELSE 0 END) AS cur_r,
  SUM(CASE WHEN ts BETWEEN datetime('now','-14 days') AND datetime('now','-7 days') THEN 1 ELSE 0 END) AS prev_r
FROM hook_metrics
WHERE ts > datetime('now','-14 days') AND step NOT IN ({sem})
GROUP BY step
HAVING (cur_f > prev_f AND (prev_f = 0 OR CAST(cur_f - prev_f AS REAL)/prev_f > {config.FAILURE_REGRESSION_PCT}))
   AND (cur_r + prev_r) >= {config.MIN_RUNS_FOR_TREND}
ORDER BY (cur_f - prev_f) DESC
LIMIT 5
""")

        return [
            FailureTrend(
                step=step,
                cur_f=_int(cf),
                prev_f=_int(pf),
                cur_r=_int(cr),
                prev_r=_int(pr),
            )
            for step, cf, pf, cr, pr in rows
        ]

    # ── failure_improvements() ───────────────────────────────────────────────

    def failure_improvements(self) -> list[FailureTrend]:
        sem = _semantic_exit_placeholders()
        rows = self._query(f"""
SELECT step,
  SUM(CASE WHEN ts > datetime('now','-7 days') AND {_failure_filter()} THEN 1 ELSE 0 END) AS cur_f,
  SUM(CASE WHEN ts BETWEEN datetime('now','-14 days') AND datetime('now','-7 days') AND {_failure_filter()} THEN 1 ELSE 0 END) AS prev_f,
  SUM(CASE WHEN ts > datetime('now','-7 days') THEN 1 ELSE 0 END) AS cur_r,
  SUM(CASE WHEN ts BETWEEN datetime('now','-14 days') AND datetime('now','-7 days') THEN 1 ELSE 0 END) AS prev_r
FROM hook_metrics
WHERE ts > datetime('now','-14 days') AND step NOT IN ({sem})
GROUP BY step
HAVING prev_f > 0 AND cur_f < prev_f AND CAST(prev_f - cur_f AS REAL)/prev_f > {config.FAILURE_REGRESSION_PCT}
   AND (cur_r + prev_r) >= {config.MIN_RUNS_FOR_TREND}
ORDER BY (prev_f - cur_f) DESC
LIMIT 3
""")

        return [
            FailureTrend(step=step, cur_f=_int(cf), prev_f=_int(pf), cur_r=_int(cr), prev_r=_int(pr))
            for step, cf, pf, cr, pr in rows
        ]

    # ── latency_regressions() ────────────────────────────────────────────────

    def latency_regressions(self) -> list[LatencyRegression]:
        rows = self._query(f"""
SELECT step,
  ROUND(AVG(CASE WHEN ts > datetime('now','-7 days') THEN duration_ms END), 0) AS cur_avg,
  ROUND(AVG(CASE WHEN ts BETWEEN datetime('now','-14 days') AND datetime('now','-7 days') THEN duration_ms END), 0) AS prev_avg,
  SUM(CASE WHEN ts > datetime('now','-7 days') THEN 1 ELSE 0 END) +
  SUM(CASE WHEN ts BETWEEN datetime('now','-14 days') AND datetime('now','-7 days') THEN 1 ELSE 0 END) AS total_n
FROM hook_metrics
WHERE ts > datetime('now','-14 days') AND duration_ms > 0
GROUP BY step
HAVING cur_avg IS NOT NULL AND prev_avg IS NOT NULL
  AND cur_avg > prev_avg
  AND CAST(cur_avg - prev_avg AS REAL) / NULLIF(prev_avg,0) > {config.REGRESSION_PCT_THRESHOLD}
  AND total_n >= {config.MIN_RUNS_FOR_TREND}
ORDER BY (cur_avg - prev_avg) DESC
LIMIT 3
""")

        return [
            LatencyRegression(
                step=step,
                cur_avg=_int(ca),
                prev_avg=_int(pa),
                total_n=_int(tn),
            )
            for step, ca, pa, tn in rows
        ]

    # ── coverage_gaps() ──────────────────────────────────────────────────────

    def coverage_gaps(self) -> list[CoverageGap]:
        rows = self._query("""
SELECT step,
  SUM(CASE WHEN ts > datetime('now','-7 days') THEN 1 ELSE 0 END) AS cur_r,
  SUM(CASE WHEN ts BETWEEN datetime('now','-14 days') AND datetime('now','-7 days') THEN 1 ELSE 0 END) AS prev_r
FROM hook_metrics WHERE ts > datetime('now','-14 days')
GROUP BY step
HAVING (cur_r = 0 AND prev_r >= 5) OR (prev_r = 0 AND cur_r >= 5)
""")

        return [
            CoverageGap(step=step, cur_r=_int(cr), prev_r=_int(pr))
            for step, cr, pr in rows
            if not re.fullmatch(config.SKIP_HOOKS_PATTERN, step)
        ]

    # ── top_failure_reasons() ─────────────────────────────────────────────────

    def top_failure_reasons(self, step: str, days: int = 7, limit: int = 5) -> list[FailureReason]:
        """Most common stderr_snippet values for a step on non-zero exits."""
        rows = self._query("""
SELECT stderr_snippet, COUNT(*) AS cnt, exit_code
FROM hook_metrics
WHERE step = ? AND exit_code != 0 AND ts > datetime('now', ? || ' days')
GROUP BY stderr_snippet, exit_code
ORDER BY cnt DESC
LIMIT ?
""", (step, f"-{days}", limit))
        return [FailureReason(snippet=str(s or ""), count=_int(c), exit_code=_int(ec))
                for s, c, ec in rows]

    # ── missing_expected_steps() ──────────────────────────────────────────────

    def missing_expected_steps(self, days: int = 7) -> list[str]:
        """Return EXPECTED_STEPS not seen in hook_metrics within the window."""
        seen = {
            row[0]
            for row in self._query(
                "SELECT DISTINCT step FROM hook_metrics WHERE ts > datetime('now', ? || ' days')",
                (f"-{days}",),
            )
        }
        skip = config.SKIP_HOOKS_PATTERN
        return sorted(
            s for s in config.EXPECTED_STEPS
            if s not in seen and not re.fullmatch(skip, s)
        )

    # ── projects_compact() ───────────────────────────────────────────────────

    def projects_compact(self) -> list[ProjectOverhead]:
        user = getpass.getuser()
        rows = self._query(f"""
SELECT
  COALESCE(NULLIF(REPLACE(repo, '/Users/{user}/Code/', ''), ''), '(global/unknown)') AS project,
  ROUND(SUM(duration_ms) / 1000.0 / 60.0, 1) AS total_min,
  COUNT(*) AS runs,
  ROUND(100.0 * SUM(CASE WHEN {_failure_filter()} THEN 1 ELSE 0 END)
        / NULLIF(COUNT(*), 0), 1) AS fail_rate
FROM hook_metrics WHERE ts > datetime('now', '-7 days')
GROUP BY repo ORDER BY SUM(duration_ms) DESC LIMIT 5
""")

        return [
            ProjectOverhead(
                project=project,
                total_min=float(total_min),
                runs=_int(runs),
                fail_rate=_opt_float(fail_rate),
            )
            for project, total_min, runs, fail_rate in rows
        ]

    # ── broken_hooks() ───────────────────────────────────────────────────────

    def broken_hooks(self) -> list[BrokenHook]:
        rows = self._query("""
WITH fails AS (
  SELECT step,
    COALESCE(NULLIF(TRIM(cmd),''), '(unknown)') AS cmd,
    COUNT(*) AS cnt,
    MAX(ts) AS last_fail
  FROM hook_metrics
  WHERE exit_code = 127 AND ts > datetime('now', '-7 days')
  GROUP BY step, cmd
  ORDER BY cnt DESC LIMIT 5
),
successes AS (
  SELECT step, MAX(ts) AS last_success
  FROM hook_metrics
  WHERE exit_code = 0 AND ts > datetime('now', '-7 days')
  GROUP BY step
)
SELECT f.step, f.cmd, f.cnt, f.last_fail, s.last_success
FROM fails f LEFT JOIN successes s ON s.step = f.step
""")

        return [
            BrokenHook(step=step, cmd=cmd, count=_int(cnt), last_fail=last_fail, last_success=last_success)
            for step, cmd, cnt, last_fail, last_success in rows
        ]

    # ── action_items() ────────────────────────────────────────────────────────

    def action_items(self) -> list[ActionItem]:
        items: list[ActionItem] = []

        # Timeout items
        timeout_rows = self._query("""
SELECT step, MAX(duration_ms) FROM hook_metrics
WHERE ts > datetime('now', '-7 days') AND duration_ms > 0
GROUP BY step
""")
        for step, maxd in timeout_rows:
            limit = config.STEP_TIMEOUTS.get(step, 0)
            if limit <= 0:
                continue
            max_i = _int(maxd)
            pct = round(max_i / limit * 100)
            if pct < 100:
                continue
            items.append(ActionItem(
                category="TIMEOUT", severity="red", step=step,
                detail=f"{step} max {max_i}ms vs {limit}ms limit ({pct}%)",
                fix="Increase timeout or optimize script",
            ))

        # Broken hooks
        for bh in self.broken_hooks():
            fix = f"Verify {bh.cmd} exists at configured path"
            if bh.last_success and bh.last_success > bh.last_fail:
                items.append(ActionItem(
                    category="BROKEN", severity="yellow", step=bh.step,
                    detail=f"{bh.step} — {bh.count} exit-127 (resolved — passing since {bh.last_success[:10]})",
                    fix=fix,
                ))
            else:
                items.append(ActionItem(
                    category="BROKEN", severity="red", step=bh.step,
                    detail=f"{bh.step} — {bh.count} exit-127 (script path missing)",
                    fix=fix,
                ))

        # Latency regressions
        regr_rows = self._query(f"""
SELECT step,
  ROUND(AVG(CASE WHEN ts > datetime('now','-7 days') THEN duration_ms END), 0) AS cur_avg,
  ROUND(AVG(CASE WHEN ts BETWEEN datetime('now','-14 days') AND datetime('now','-7 days') THEN duration_ms END), 0) AS prev_avg,
  ROUND(SUM(CASE WHEN ts > datetime('now','-7 days') THEN 1 ELSE 0 END) *
    (AVG(CASE WHEN ts > datetime('now','-7 days') THEN duration_ms END) -
     AVG(CASE WHEN ts BETWEEN datetime('now','-14 days') AND datetime('now','-7 days') THEN duration_ms END)) / 1000.0, 0) AS impact_s
FROM hook_metrics
WHERE ts > datetime('now','-14 days') AND duration_ms > 0
GROUP BY step
HAVING cur_avg IS NOT NULL AND prev_avg IS NOT NULL
  AND cur_avg > prev_avg
  AND CAST(cur_avg - prev_avg AS REAL) / NULLIF(prev_avg, 0) > {config.REGRESSION_PCT_THRESHOLD}
  AND (SUM(CASE WHEN ts > datetime('now','-7 days') THEN 1 ELSE 0 END) +
       SUM(CASE WHEN ts BETWEEN datetime('now','-14 days') AND datetime('now','-7 days') THEN 1 ELSE 0 END)) >= {config.MIN_RUNS_FOR_TREND}
  AND impact_s >= {config.IMPACT_THRESHOLD_S}
ORDER BY impact_s DESC
LIMIT 5
""")
        for step, ca, pa, impact_s in regr_rows:
            ca_i = _int(ca)
            pa_i = _int(pa)
            impact_i = _int(impact_s)
            delta_ms = ca_i - pa_i
            if ca_i < 1000:
                detail = f"{step} avg +{delta_ms}ms ({pa_i}ms->{ca_i}ms), {impact_i}s cumulative"
            else:
                detail = f"{step} avg +{delta_ms // 1000}s ({pa_i // 1000}s->{ca_i // 1000}s), {impact_i}s cumulative"
            items.append(ActionItem(
                category="SLOW", severity="yellow", step=step,
                detail=detail,
                fix="Investigate latency increase",
            ))

        # Reliability failures
        fail_rows = self._query(f"""
SELECT step, COUNT(*) AS cnt
FROM hook_metrics
WHERE {_failure_filter()} AND exit_code != 127
  AND ts > datetime('now', '-1 day')
GROUP BY step
ORDER BY cnt DESC
LIMIT 5
""")
        for step, count in fail_rows:
            cnt = _int(count)
            word = "failure" if cnt == 1 else "failures"
            reasons = self.top_failure_reasons(step)
            top_error = ""
            if reasons and reasons[0].snippet:
                r = reasons[0]
                code_label = config.EXIT_CODE_LABELS.get(r.exit_code or 0, f"exit {r.exit_code}")
                top_error = f" [{code_label}: \"{r.snippet[:60]}\" \u00d7{r.count}]"
            items.append(ActionItem(
                category="FAIL", severity="red", step=step,
                detail=f"{step} — {cnt} {word} (24h){top_error}",
                fix="Investigate hook failures",
            ))

        # Missing expected steps
        for step in self.missing_expected_steps():
            items.append(ActionItem(
                category="MISSING", severity="yellow", step=step,
                detail=f"{step} — no runs in 7d (expected)",
                fix="Verify hook is wired in settings.json",
            ))

        return items

    # ── health_24h() ──────────────────────────────────────────────────────────

    def health_24h(self) -> HealthSummary:
        sem = _semantic_exit_placeholders()
        row = self._query_one(f"""
SELECT
  COUNT(*) AS total_runs,
  SUM(CASE WHEN {_failure_filter()} THEN 1 ELSE 0 END) AS failures,
  ROUND(100.0 * SUM(CASE WHEN {_failure_filter()} THEN 1 ELSE 0 END)
        / NULLIF(COUNT(*), 0), 1) AS fail_pct,
  SUM(CASE WHEN exit_code != 0 AND step IN ({sem}) THEN 1 ELSE 0 END) AS review_findings,
  SUM(CASE WHEN step IN ({sem}) THEN 1 ELSE 0 END) AS review_runs,
  COALESCE(SUM(duration_ms), 0) AS total_overhead_ms,
  COALESCE(MAX(duration_ms), 0) AS max_latency_ms,
  SUM(CASE WHEN duration_ms > {config.SLOW_RUN_MS} THEN 1 ELSE 0 END) AS slow_count
FROM hook_metrics
WHERE ts > datetime('now', '-1 day')
""")
        # aggregate without GROUP BY always returns exactly one row
        assert row is not None
        return HealthSummary(
            total=_int(row[0]),
            failures=_int(row[1]),
            fail_pct=_opt_float(row[2]),
            review_findings=_int(row[3]),
            review_runs=_int(row[4]),
            overhead_ms=_int(row[5]),
            max_latency_ms=_int(row[6]),
            slow_count=_int(row[7]),
        )

    # ── Step / Repo / Session analysis ────────────────────────────────────────

    def step_reliability(self, days: int = 7, repo: Optional[str] = None) -> list[StepReliability]:
        repo_filter = "AND repo = ?" if repo else ""
        day_str = f"-{days} days"
        params: list = [day_str, day_str]
        if repo:
            params = [day_str, repo, day_str, repo]
        rows = self._query(f"""
WITH stats AS (
  SELECT step,
    COUNT(*) AS total_runs,
    SUM(CASE WHEN {_failure_filter()} THEN 1 ELSE 0 END) AS failures,
    ROUND(AVG(duration_ms), 1) AS avg_ms,
    MAX(duration_ms) AS max_ms,
    ROUND(SUM(duration_ms) / 1000.0, 2) AS total_s
  FROM hook_metrics
  WHERE ts > datetime('now', ?) {repo_filter}
  GROUP BY step
),
ranked AS (
  SELECT step, duration_ms,
    ROW_NUMBER() OVER (PARTITION BY step ORDER BY duration_ms) AS rn,
    COUNT(*) OVER (PARTITION BY step) AS cnt
  FROM hook_metrics
  WHERE ts > datetime('now', ?) AND duration_ms > 0 {repo_filter}
)
SELECT
  s.step,
  s.total_runs,
  s.failures,
  ROUND(100.0 * s.failures / NULLIF(s.total_runs, 0), 1) AS fail_rate,
  COALESCE(MAX(CASE WHEN r.rn = CAST(CEIL(0.50 * r.cnt) AS INTEGER) THEN r.duration_ms END), 0) AS p50_ms,
  COALESCE(MAX(CASE WHEN r.rn = CAST(CEIL(0.90 * r.cnt) AS INTEGER) THEN r.duration_ms END), 0) AS p90_ms,
  COALESCE(MAX(CASE WHEN r.rn = CAST(CEIL(0.99 * r.cnt) AS INTEGER) THEN r.duration_ms END), 0) AS p99_ms,
  s.avg_ms,
  s.max_ms,
  s.total_s
FROM stats s
LEFT JOIN ranked r ON s.step = r.step
GROUP BY s.step
ORDER BY s.failures DESC, s.avg_ms DESC
""", tuple(params))
        result = []
        for row in rows:
            step, total_runs, failures, fail_rate, p50, p90, p99, avg_ms, max_ms, total_s = row
            fr = _opt_float(fail_rate)
            pain = float(total_s or 0) * (fr / 100.0) if fr is not None else 0.0
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
                total_s=float(total_s or 0),
                pain_index=round(pain, 2),
            ))
        return result

    def step_drilldown(self, step: str, days: int = 7) -> dict:
        day_str = f"-{days} days"
        by_repo = self._query(f"""
SELECT repo, COUNT(*) AS runs,
  SUM(CASE WHEN {_failure_filter()} THEN 1 ELSE 0 END) AS failures,
  ROUND(AVG(duration_ms), 1) AS avg_ms
FROM hook_metrics
WHERE step = ? AND ts > datetime('now', ?)
GROUP BY repo ORDER BY runs DESC LIMIT 10
""", (step, day_str))
        daily = self._query(f"""
SELECT DATE(ts) AS day, COUNT(*) AS runs,
  SUM(CASE WHEN {_failure_filter()} THEN 1 ELSE 0 END) AS failures
FROM hook_metrics
WHERE step = ? AND ts > datetime('now', ?)
GROUP BY day ORDER BY day
""", (step, day_str))
        exit_codes = self._query("""
SELECT exit_code, COUNT(*) AS count
FROM hook_metrics
WHERE step = ? AND ts > datetime('now', ?)
GROUP BY exit_code ORDER BY count DESC
""", (step, day_str))
        return {
            "by_repo": [(repo, _int(runs), _int(failures), float(avg or 0))
                        for repo, runs, failures, avg in by_repo],
            "daily": [(day, _int(runs), _int(failures))
                      for day, runs, failures in daily],
            "exit_codes": [(int(code) if code is not None else None, _int(cnt))
                           for code, cnt in exit_codes],
        }

    def repo_profiles(self, days: int = 7) -> list[RepoProfile]:
        user = getpass.getuser()
        has_session = self._has_session_column()
        session_col = "COUNT(DISTINCT session)" if has_session else "0"
        day_str = f"-{days} days"
        rows = self._query(f"""
SELECT
  COALESCE(NULLIF(REPLACE(repo, '/Users/{user}/Code/', ''), ''), '(global/unknown)') AS project,
  COUNT(*) AS total_runs,
  SUM(CASE WHEN {_failure_filter()} THEN 1 ELSE 0 END) AS failures,
  ROUND(100.0 * SUM(CASE WHEN {_failure_filter()} THEN 1 ELSE 0 END)
        / NULLIF(COUNT(*), 0), 1) AS fail_rate,
  COUNT(DISTINCT step) AS distinct_steps,
  COALESCE(SUM(duration_ms), 0) AS overhead_ms,
  ROUND(SUM(duration_ms) / 1000.0 / 60.0, 2) AS overhead_min,
  {session_col} AS session_count
FROM hook_metrics WHERE ts > datetime('now', ?)
GROUP BY repo ORDER BY SUM(duration_ms) DESC
""", (day_str,))
        result = []
        for row in rows:
            project, total_runs, failures, fail_rate, distinct_steps, overhead_ms, overhead_min, session_count = row
            sc = _int(session_count)
            density = _int(distinct_steps) / sc if sc > 0 else 0.0
            result.append(RepoProfile(
                repo=project,
                total_runs=_int(total_runs),
                failures=_int(failures),
                fail_rate=_opt_float(fail_rate),
                distinct_steps=_int(distinct_steps),
                overhead_ms=_int(overhead_ms),
                overhead_min=float(overhead_min or 0),
                session_count=sc,
                guardrail_density=round(density, 2),
            ))
        return result

    def under_instrumented_repos(self, days: int = 7) -> list[tuple[str, int]]:
        user = getpass.getuser()
        day_str = f"-{days} days"
        rows = self._query(f"""
SELECT
  COALESCE(NULLIF(REPLACE(repo, '/Users/{user}/Code/', ''), ''), '(global/unknown)') AS project,
  COUNT(DISTINCT step) AS distinct_steps,
  COUNT(*) AS runs
FROM hook_metrics WHERE ts > datetime('now', ?)
GROUP BY repo
HAVING distinct_steps < {config.MIN_STEPS_FOR_COVERAGE}
   AND runs >= {config.MIN_RUNS_FOR_TREND}
ORDER BY runs DESC
""", (day_str,))
        return [(project, _int(steps)) for project, steps, _ in rows]

    def session_list(self, days: int = 7, limit: int = 20) -> list[SessionSummary]:
        if not self._has_session_column():
            return []
        day_str = f"-{days} days"
        rows = self._query(f"""
SELECT
  h.session,
  MIN(h.ts) AS first_ts,
  MAX(h.ts) AS last_ts,
  CAST((JULIANDAY(MAX(h.ts)) - JULIANDAY(MIN(h.ts))) * 86400 AS INTEGER) AS duration_s,
  COUNT(DISTINCT h.id) AS hook_runs,
  SUM(CASE WHEN {_failure_filter("h.step")} THEN 1 ELSE 0 END) AS hook_failures,
  COUNT(DISTINCT a.id) AS tool_uses,
  COALESCE(SUM(h.duration_ms), 0) AS overhead_ms,
  COUNT(DISTINCT h.step) AS distinct_steps
FROM hook_metrics h
LEFT JOIN audit_events a ON h.session = a.session
WHERE h.session IS NOT NULL AND h.ts > datetime('now', ?)
GROUP BY h.session
ORDER BY hook_failures DESC, overhead_ms DESC
LIMIT ?
""", (day_str, limit))
        return [
            SessionSummary(
                session_id=session_id,
                first_ts=first_ts,
                last_ts=last_ts,
                duration_s=_int(duration_s),
                hook_runs=_int(hook_runs),
                hook_failures=_int(hook_failures),
                tool_uses=_int(tool_uses),
                overhead_ms=_int(overhead_ms),
                distinct_steps=_int(distinct_steps),
            )
            for session_id, first_ts, last_ts, duration_s, hook_runs,
                hook_failures, tool_uses, overhead_ms, distinct_steps in rows
        ]

    def session_timeline(self, session_id: str) -> list[SessionTimeline]:
        if not self._has_session_column():
            return []
        rows = self._query("""
SELECT ts, 'hook' AS source, step AS name, duration_ms, exit_code,
  SUBSTR(COALESCE(cmd, ''), 1, 120) AS detail
FROM hook_metrics
WHERE session = ?
UNION ALL
SELECT ts, 'tool' AS source, tool AS name, 0 AS duration_ms, NULL AS exit_code,
  SUBSTR(COALESCE(input, ''), 1, 120) AS detail
FROM audit_events
WHERE session = ?
ORDER BY ts
""", (session_id, session_id))
        return [
            SessionTimeline(
                ts=ts,
                source=source,
                name=name,
                duration_ms=_int(duration_ms),
                exit_code=int(exit_code) if exit_code is not None else None,
                detail=detail or "",
            )
            for ts, source, name, duration_ms, exit_code, detail in rows
        ]

    # ── Advisor methods ───────────────────────────────────────────────────────

    def hot_sequences(self, days: int = 7) -> list[HotSequence]:
        """Find step pairs that frequently fail together within sessions."""
        if not self._has_session_column():
            return []
        sem = _semantic_exit_placeholders()
        day_str = f"-{days} days"
        rows = self._query(f"""
WITH ordered AS (
    SELECT session, step, exit_code,
           LAG(step) OVER (PARTITION BY session ORDER BY ts, id) AS prev_step
    FROM hook_metrics
    WHERE ts > datetime('now', ?) AND step NOT IN ({sem})
      AND session != ''
)
SELECT prev_step, step,
       COUNT(*) AS total,
       SUM(CASE WHEN {_failure_filter()} THEN 1 ELSE 0 END) AS failures,
       ROUND(100.0 * SUM(CASE WHEN {_failure_filter()} THEN 1 ELSE 0 END) / COUNT(*), 1) AS fail_rate
FROM ordered
WHERE prev_step IS NOT NULL
GROUP BY prev_step, step
HAVING COUNT(*) >= ? AND fail_rate >= ?
ORDER BY failures DESC
LIMIT 10
""", (day_str, config.MIN_RUNS_FOR_TREND, config.HOT_SEQUENCE_FAIL_RATE))
        return [HotSequence(
            prev_step=r[0], step=r[1], total=_int(r[2]),
            failures=_int(r[3]), fail_rate=float(r[4]),
        ) for r in rows]

    def period_aggregate(self, days: int = 7) -> PeriodAggregate:
        """Aggregate hook metrics over a time window."""
        day_str = f"-{days} days"
        row = self._query_one(f"""
SELECT
    COUNT(*) AS total_runs,
    SUM(CASE WHEN {_failure_filter()} THEN 1 ELSE 0 END) AS failures,
    ROUND(100.0 * SUM(CASE WHEN {_failure_filter()} THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 1) AS fail_rate,
    COALESCE(SUM(duration_ms), 0) AS overhead_ms,
    COUNT(DISTINCT step) AS unique_steps,
    COUNT(DISTINCT repo) AS unique_repos,
    MIN(ts) AS start_ts,
    MAX(ts) AS end_ts
FROM hook_metrics
WHERE ts > datetime('now', ?)
""", (day_str,))
        if row is None or row[0] == 0:
            return PeriodAggregate(0, 0, 0.0, 0, 0, 0, "", "")
        return PeriodAggregate(
            total_runs=_int(row[0]),
            failures=_int(row[1]),
            fail_rate=float(row[2]) if row[2] is not None else 0.0,
            overhead_ms=_int(row[3]),
            unique_steps=_int(row[4]),
            unique_repos=_int(row[5]),
            start_ts=row[6] or "",
            end_ts=row[7] or "",
        )

    # ── Verbose failure section helpers ───────────────────────────────────────

    def failures_by_step(self) -> list[tuple[str, int]]:
        rows = self._query(f"""
SELECT step, COUNT(*) AS failures
FROM hook_metrics
WHERE {_failure_filter()}
GROUP BY step ORDER BY failures DESC
""")
        return [(step, _int(cnt)) for step, cnt in rows]

    def exit_codes_by_step(self) -> list[tuple[str, int, int]]:
        rows = self._query(f"""
SELECT step, exit_code, COUNT(*) AS count
FROM hook_metrics
WHERE {_failure_filter()}
GROUP BY step, exit_code ORDER BY count DESC
""")
        return [(step, _int(code), _int(cnt)) for step, code, cnt in rows]

    def review_hook_stats(self) -> list[tuple[str, int, int, float]]:
        sem = _semantic_exit_placeholders()
        rows = self._query(f"""
SELECT step, COUNT(*) AS total_runs,
  SUM(CASE WHEN exit_code != 0 THEN 1 ELSE 0 END) AS findings,
  ROUND(100.0 * SUM(CASE WHEN exit_code != 0 THEN 1 ELSE 0 END)
        / NULLIF(COUNT(*), 0), 1) AS findings_pct
FROM hook_metrics WHERE step IN ({sem})
GROUP BY step
""")
        return [(step, _int(runs), _int(findings), float(pct or 0))
                for step, runs, findings, pct in rows]

    def exit127_cmds(self) -> list[tuple[str, int]]:
        rows = self._query("""
SELECT cmd, COUNT(*) AS count
FROM hook_metrics WHERE exit_code = 127
GROUP BY cmd ORDER BY count DESC LIMIT 10
""")
        return [(cmd, _int(cnt)) for cmd, cnt in rows]

    def near_timeout_rows(self) -> list[tuple[str, int, str]]:
        rows = self._query("""
SELECT step, duration_ms, ts
FROM hook_metrics WHERE duration_ms > 1500
ORDER BY duration_ms DESC LIMIT 10
""")
        return [(step, _int(dur), ts) for step, dur, ts in rows]

    # ── Verbose performance section helpers ───────────────────────────────────

    def perf_full(self) -> list[tuple[str, float, Optional[float], int, int]]:
        rows = self._query("""
WITH ranked AS (
  SELECT step, duration_ms,
    ROW_NUMBER() OVER (PARTITION BY step ORDER BY duration_ms) AS rn,
    COUNT(*) OVER (PARTITION BY step) AS cnt
  FROM hook_metrics WHERE duration_ms > 0
)
SELECT step,
  ROUND(AVG(duration_ms), 1) AS avg_ms,
  MAX(CASE WHEN rn = CAST(CEIL(0.95 * cnt) AS INTEGER) THEN duration_ms END) AS p95_ms,
  MAX(duration_ms) AS max_ms,
  MAX(cnt) AS total_n
FROM ranked GROUP BY step ORDER BY avg_ms DESC
""")
        return [(step, float(avg), _opt_float(p95), _int(maxd), _int(total))
                for step, avg, p95, maxd, total in rows]

    def max_duration_by_step(self) -> list[tuple[str, int]]:
        rows = self._query("""
SELECT step, MAX(duration_ms) AS max_ms
FROM hook_metrics WHERE duration_ms > 0
GROUP BY step
""")
        return [(step, _int(maxd)) for step, maxd in rows]

    # ── Verbose usage section helpers ─────────────────────────────────────────

    def tool_distribution(self) -> list[tuple[str, int]]:
        rows = self._query("""
SELECT tool, COUNT(*) AS count
FROM audit_events GROUP BY tool ORDER BY count DESC
""")
        return [(tool, _int(cnt)) for tool, cnt in rows]

    def session_stats_7d(self) -> tuple[int, int, float]:
        row = self._query_one("""
SELECT
  COUNT(DISTINCT session) AS sessions,
  COUNT(*) AS total_events,
  ROUND(1.0 * COUNT(*) / NULLIF(COUNT(DISTINCT session), 0), 1) AS avg_per_session
FROM audit_events WHERE ts > datetime('now','-7 days')
""")
        # aggregate without GROUP BY always returns exactly one row
        assert row is not None
        return (_int(row[0]), _int(row[1]), float(row[2] or 0))

    def most_edited_files(self) -> list[tuple[str, int]]:
        rows = self._query("""
SELECT json_extract(input, '$.file_path') AS file_path, COUNT(*) AS count
FROM audit_events
WHERE tool IN ('Edit','Write')
  AND json_extract(input, '$.file_path') IS NOT NULL
GROUP BY file_path ORDER BY count DESC LIMIT 10
""")
        return [(fpath, _int(cnt)) for fpath, cnt in rows]

    def bash_cmd_categories(self) -> list[tuple[str, int]]:
        rows = self._query("""
SELECT
  TRIM(SUBSTR(
    json_extract(input, '$.command'),
    1, INSTR(json_extract(input, '$.command') || ' ', ' ') - 1
  )) AS category,
  COUNT(*) AS count
FROM audit_events
WHERE tool = 'Bash'
  AND json_extract(input, '$.command') IS NOT NULL
  AND json_extract(input, '$.command') != ''
GROUP BY category ORDER BY count DESC LIMIT 15
""")
        return [(cat, _int(cnt)) for cat, cnt in rows]

    # ── Verbose data quality helpers ──────────────────────────────────────────

    def zero_timing_count(self) -> int:
        row = self._query_one(
            "SELECT COUNT(*) FROM hook_metrics WHERE duration_ms = 0 AND real_s = 0"
        )
        # aggregate without GROUP BY always returns exactly one row
        assert row is not None
        return _int(row[0])

    def unknown_hook_count(self) -> int:
        row = self._query_one(
            "SELECT COUNT(*) FROM hook_metrics WHERE hook = '' OR hook IS NULL"
        )
        # aggregate without GROUP BY always returns exactly one row
        assert row is not None
        return _int(row[0])

    def duplicate_rows(self) -> list[tuple[str, int, str, int]]:
        rows = self._query("""
SELECT step, exit_code, strftime('%Y-%m-%dT%H:%M:%S', ts) AS ts_sec, COUNT(*) AS n
FROM hook_metrics
GROUP BY step, exit_code, ts_sec
HAVING n > 1 ORDER BY n DESC LIMIT 10
""")
        return [(step, _int(code), ts_sec, _int(n)) for step, code, ts_sec, n in rows]

    # ── Verbose project helpers ───────────────────────────────────────────────

    def projects_full(self) -> list[tuple[str, int, float, int, int]]:
        user = getpass.getuser()
        rows = self._query(f"""
SELECT
  COALESCE(NULLIF(REPLACE(repo, '/Users/{user}/Code/', ''), ''), '(global/unknown)') AS project,
  SUM(duration_ms) AS total_ms,
  ROUND(SUM(duration_ms) / 1000.0 / 60.0, 1) AS total_min,
  COUNT(*) AS runs,
  SUM(CASE WHEN {_failure_filter()} THEN 1 ELSE 0 END) AS failures
FROM hook_metrics WHERE ts > datetime('now', '-7 days')
GROUP BY repo ORDER BY total_ms DESC LIMIT 15
""")
        return [(proj, _int(tms), float(tmin), _int(runs), _int(fails))
                for proj, tms, tmin, runs, fails in rows]

    def top_steps_per_project(self) -> list[tuple[str, str, int, int]]:
        user = getpass.getuser()
        rows = self._query(f"""
SELECT
  COALESCE(NULLIF(REPLACE(repo, '/Users/{user}/Code/', ''), ''), '(global/unknown)') AS project,
  step, COUNT(*) AS runs, SUM(duration_ms) AS total_ms
FROM hook_metrics WHERE ts > datetime('now', '-7 days')
GROUP BY repo, step ORDER BY repo, total_ms DESC
""")
        result: list[tuple[str, str, int, int]] = []
        counts: dict[str, int] = {}
        for proj, step, runs, tms in rows:
            counts[proj] = counts.get(proj, 0) + 1
            if counts[proj] <= 3:
                result.append((proj, step, _int(runs), _int(tms)))
        return result

    # ── Verbose trend helpers (no LIMIT) ──────────────────────────────────────

    def failure_regressions_full(self) -> list[FailureTrend]:
        sem = _semantic_exit_placeholders()
        rows = self._query(f"""
SELECT step,
  SUM(CASE WHEN ts > datetime('now','-7 days') AND {_failure_filter()} THEN 1 ELSE 0 END) AS cur_f,
  SUM(CASE WHEN ts BETWEEN datetime('now','-14 days') AND datetime('now','-7 days') AND {_failure_filter()} THEN 1 ELSE 0 END) AS prev_f,
  SUM(CASE WHEN ts > datetime('now','-7 days') THEN 1 ELSE 0 END) AS cur_r,
  SUM(CASE WHEN ts BETWEEN datetime('now','-14 days') AND datetime('now','-7 days') THEN 1 ELSE 0 END) AS prev_r
FROM hook_metrics
WHERE ts > datetime('now','-14 days') AND step NOT IN ({sem})
GROUP BY step
HAVING (cur_f > prev_f AND (prev_f = 0 OR CAST(cur_f - prev_f AS REAL)/prev_f > {config.FAILURE_REGRESSION_PCT}))
   AND (cur_r + prev_r) >= {config.MIN_RUNS_FOR_TREND}
ORDER BY (cur_f - prev_f) DESC
""")
        return [FailureTrend(step=step, cur_f=_int(cf), prev_f=_int(pf), cur_r=_int(cr), prev_r=_int(pr))
                for step, cf, pf, cr, pr in rows]

    def failure_improvements_full(self) -> list[FailureTrend]:
        sem = _semantic_exit_placeholders()
        rows = self._query(f"""
SELECT step,
  SUM(CASE WHEN ts > datetime('now','-7 days') AND {_failure_filter()} THEN 1 ELSE 0 END) AS cur_f,
  SUM(CASE WHEN ts BETWEEN datetime('now','-14 days') AND datetime('now','-7 days') AND {_failure_filter()} THEN 1 ELSE 0 END) AS prev_f,
  SUM(CASE WHEN ts > datetime('now','-7 days') THEN 1 ELSE 0 END) AS cur_r,
  SUM(CASE WHEN ts BETWEEN datetime('now','-14 days') AND datetime('now','-7 days') THEN 1 ELSE 0 END) AS prev_r
FROM hook_metrics
WHERE ts > datetime('now','-14 days') AND step NOT IN ({sem})
GROUP BY step
HAVING prev_f > 0 AND cur_f < prev_f AND CAST(prev_f - cur_f AS REAL)/prev_f > {config.FAILURE_REGRESSION_PCT}
   AND (cur_r + prev_r) >= {config.MIN_RUNS_FOR_TREND}
ORDER BY (prev_f - cur_f) DESC
""")
        return [FailureTrend(step=step, cur_f=_int(cf), prev_f=_int(pf), cur_r=_int(cr), prev_r=_int(pr))
                for step, cf, pf, cr, pr in rows]

    def latency_regressions_full(self) -> list[LatencyRegression]:
        rows = self._query(f"""
SELECT step,
  ROUND(AVG(CASE WHEN ts > datetime('now','-7 days') THEN duration_ms END), 0) AS cur_avg,
  ROUND(AVG(CASE WHEN ts BETWEEN datetime('now','-14 days') AND datetime('now','-7 days') THEN duration_ms END), 0) AS prev_avg,
  SUM(CASE WHEN ts > datetime('now','-7 days') THEN 1 ELSE 0 END) +
  SUM(CASE WHEN ts BETWEEN datetime('now','-14 days') AND datetime('now','-7 days') THEN 1 ELSE 0 END) AS total_n
FROM hook_metrics
WHERE ts > datetime('now','-14 days') AND duration_ms > 0
GROUP BY step
HAVING cur_avg IS NOT NULL AND prev_avg IS NOT NULL
  AND cur_avg > prev_avg
  AND CAST(cur_avg - prev_avg AS REAL) / NULLIF(prev_avg,0) > {config.REGRESSION_PCT_THRESHOLD}
  AND total_n >= {config.MIN_RUNS_FOR_TREND}
ORDER BY (cur_avg - prev_avg) DESC
""")
        return [LatencyRegression(step=step, cur_avg=_int(ca), prev_avg=_int(pa), total_n=_int(tn))
                for step, ca, pa, tn in rows]

    # ── export_data() ────────────────────────────────────────────────────────

    def guardrail_summary(self, days: int = 7) -> list[GuardrailSummary]:
        if not config.GUARDRAIL_STEPS:
            return []
        placeholders = ", ".join("?" for _ in config.GUARDRAIL_STEPS)
        day_str = f"-{days} days"
        rows = self._query(f"""
SELECT step,
  COUNT(*) AS total_runs,
  SUM(CASE WHEN exit_code = 2 THEN 1 ELSE 0 END) AS blocks,
  ROUND(100.0 * SUM(CASE WHEN exit_code = 2 THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 1) AS block_rate,
  ROUND(AVG(duration_ms), 1) AS avg_ms
FROM hook_metrics
WHERE step IN ({placeholders}) AND ts > datetime('now', ?)
GROUP BY step ORDER BY total_runs DESC
""", (*tuple(config.GUARDRAIL_STEPS), day_str))
        return [
            GuardrailSummary(step=step, total_runs=_int(tr), blocks=_int(bl),
                             block_rate=_opt_float(br), avg_ms=float(am))
            for step, tr, bl, br, am in rows
        ]

    def event_distribution(self, days: int = 7) -> list[tuple[str, int]]:
        day_str = f"-{days} days"
        rows = self._query("""
SELECT hook, COUNT(*) AS cnt
FROM hook_metrics
WHERE ts > datetime('now', ?)
GROUP BY hook ORDER BY cnt DESC
""", (day_str,))
        return [(hook, _int(cnt)) for hook, cnt in rows]

    def export_data(self) -> dict:
        sem = _semantic_exit_placeholders()
        ts_now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        row = self._query_one(f"""
SELECT
  SUM(CASE WHEN ts > datetime('now','-7 days') THEN 1 ELSE 0 END) AS cur_runs,
  SUM(CASE WHEN ts BETWEEN datetime('now','-14 days') AND datetime('now','-7 days') THEN 1 ELSE 0 END) AS prev_runs,
  SUM(CASE WHEN ts > datetime('now','-7 days') AND {_failure_filter()} THEN 1 ELSE 0 END) AS cur_fail,
  SUM(CASE WHEN ts BETWEEN datetime('now','-14 days') AND datetime('now','-7 days') AND {_failure_filter()} THEN 1 ELSE 0 END) AS prev_fail,
  ROUND(100.0 * SUM(CASE WHEN ts > datetime('now','-7 days') AND {_failure_filter()} THEN 1 ELSE 0 END) / NULLIF(SUM(CASE WHEN ts > datetime('now','-7 days') THEN 1 ELSE 0 END),0),1) AS cur_rate,
  ROUND(100.0 * SUM(CASE WHEN ts BETWEEN datetime('now','-14 days') AND datetime('now','-7 days') AND {_failure_filter()} THEN 1 ELSE 0 END) / NULLIF(SUM(CASE WHEN ts BETWEEN datetime('now','-14 days') AND datetime('now','-7 days') THEN 1 ELSE 0 END),0),1) AS prev_rate,
  SUM(CASE WHEN ts > datetime('now','-7 days') THEN duration_ms ELSE 0 END) AS cur_ms,
  SUM(CASE WHEN ts BETWEEN datetime('now','-14 days') AND datetime('now','-7 days') THEN duration_ms ELSE 0 END) AS prev_ms,
  SUM(CASE WHEN ts > datetime('now','-7 days') AND duration_ms > {config.SLOW_RUN_MS} THEN 1 ELSE 0 END) AS cur_slow,
  SUM(CASE WHEN ts BETWEEN datetime('now','-14 days') AND datetime('now','-7 days') AND duration_ms > {config.SLOW_RUN_MS} THEN 1 ELSE 0 END) AS prev_slow
FROM hook_metrics WHERE ts > datetime('now','-14 days')
""")

        # aggregate without GROUP BY always returns exactly one row
        assert row is not None
        cur_runs = _int(row[0])
        prev_runs = _int(row[1])
        cur_fail = _int(row[2])
        prev_fail = _int(row[3])
        cur_rate = _opt_float(row[4])
        prev_rate = _opt_float(row[5])
        cur_ms = _int(row[6])
        prev_ms = _int(row[7])
        cur_slow = _int(row[8])
        prev_slow = _int(row[9])

        fail_rows = self._query(f"""
SELECT step,
  SUM(CASE WHEN ts > datetime('now','-7 days') AND {_failure_filter()} THEN 1 ELSE 0 END) AS cur_f,
  SUM(CASE WHEN ts BETWEEN datetime('now','-14 days') AND datetime('now','-7 days') AND {_failure_filter()} THEN 1 ELSE 0 END) AS prev_f,
  SUM(CASE WHEN ts > datetime('now','-7 days') THEN 1 ELSE 0 END) AS cur_r,
  SUM(CASE WHEN ts BETWEEN datetime('now','-14 days') AND datetime('now','-7 days') THEN 1 ELSE 0 END) AS prev_r
FROM hook_metrics
WHERE ts > datetime('now','-14 days') AND step NOT IN ({sem})
GROUP BY step
HAVING (cur_f != prev_f) AND (cur_r + prev_r) >= {config.MIN_RUNS_FOR_TREND}
ORDER BY ABS(cur_f - prev_f) DESC
""")

        failure_trends = []
        for step, cf, pf, cr, pr in fail_rows:
            cf_i = _int(cf)
            pf_i = _int(pf)
            delta = cf_i - pf_i
            pct_change = None if pf_i == 0 else round((cf_i - pf_i) / pf_i * 100, 1)
            direction = "regression" if cf_i > pf_i else "improvement"
            failure_trends.append({
                "hook.step": step,
                "current": {"claude.hooks.failures": cf_i, "claude.hooks.runs": _int(cr)},
                "previous": {"claude.hooks.failures": pf_i, "claude.hooks.runs": _int(pr)},
                "delta": delta,
                "pct_change": pct_change,
                "direction": direction,
            })

        # Latency trends (both directions, ABS comparison)
        lat_rows = self._query(f"""
SELECT step,
  ROUND(AVG(CASE WHEN ts > datetime('now','-7 days') THEN duration_ms END), 0) AS cur_avg,
  ROUND(AVG(CASE WHEN ts BETWEEN datetime('now','-14 days') AND datetime('now','-7 days') THEN duration_ms END), 0) AS prev_avg
FROM hook_metrics
WHERE ts > datetime('now','-14 days') AND duration_ms > 0
GROUP BY step
HAVING cur_avg IS NOT NULL AND prev_avg IS NOT NULL
  AND ABS(cur_avg - prev_avg) / NULLIF(prev_avg, 0) > {config.REGRESSION_PCT_THRESHOLD}
  AND (SUM(CASE WHEN ts > datetime('now','-7 days') THEN 1 ELSE 0 END) +
       SUM(CASE WHEN ts BETWEEN datetime('now','-14 days') AND datetime('now','-7 days') THEN 1 ELSE 0 END)) >= {config.MIN_RUNS_FOR_TREND}
ORDER BY ABS(cur_avg - prev_avg) DESC
""")

        latency_trends = []
        for step, ca, pa in lat_rows:
            ca_i = _int(ca)
            pa_i = _int(pa)
            delta_ms = ca_i - pa_i
            pct_change = round((ca_i - pa_i) / pa_i * 100, 1) if pa_i != 0 else None
            direction = "regression" if ca_i > pa_i else "improvement"
            latency_trends.append({
                "hook.step": step,
                "current": {"claude.hooks.duration.avg_ms": ca_i},
                "previous": {"claude.hooks.duration.avg_ms": pa_i},
                "delta_ms": delta_ms,
                "pct_change": pct_change,
                "direction": direction,
            })

        # Coverage gaps (no SKIP_HOOKS filtering for export)
        gap_rows = self._query("""
SELECT step,
  SUM(CASE WHEN ts > datetime('now','-7 days') THEN 1 ELSE 0 END) AS cur_r,
  SUM(CASE WHEN ts BETWEEN datetime('now','-14 days') AND datetime('now','-7 days') THEN 1 ELSE 0 END) AS prev_r
FROM hook_metrics WHERE ts > datetime('now','-14 days')
GROUP BY step
HAVING (cur_r = 0 AND prev_r >= 5) OR (prev_r = 0 AND cur_r >= 5)
""")

        coverage_gaps = []
        for step, cr, pr in gap_rows:
            cr_i = _int(cr)
            pr_i = _int(pr)
            if cr_i == 0:
                coverage_gaps.append({
                    "hook.step": step,
                    "previous_runs": pr_i,
                    "status": "stopped",
                })
            else:
                coverage_gaps.append({
                    "hook.step": step,
                    "current_runs": cr_i,
                    "status": "new",
                })

        return {
            "schema": "claude.hooks.trends/v1",
            "generated_at": ts_now,
            "period": {
                "current": {"start": "-7d", "end": "now"},
                "previous": {"start": "-14d", "end": "-7d"},
            },
            "summary": {
                "current": {
                    "claude.hooks.runs": cur_runs,
                    "claude.hooks.failures": cur_fail,
                    "claude.hooks.failure_rate": cur_rate,
                    "claude.hooks.overhead_ms": cur_ms,
                    "claude.hooks.slow_runs": cur_slow,
                },
                "previous": {
                    "claude.hooks.runs": prev_runs,
                    "claude.hooks.failures": prev_fail,
                    "claude.hooks.failure_rate": prev_rate,
                    "claude.hooks.overhead_ms": prev_ms,
                    "claude.hooks.slow_runs": prev_slow,
                },
            },
            "failure_trends": failure_trends,
            "latency_trends": latency_trends,
            "coverage_gaps": coverage_gaps,
            "guardrails": [
                {
                    "hook.step": g.step,
                    "claude.hooks.runs": g.total_runs,
                    "claude.hooks.blocks": g.blocks,
                    "claude.hooks.block_rate": g.block_rate,
                    "claude.hooks.duration.avg_ms": g.avg_ms,
                }
                for g in self.guardrail_summary()
            ],
            "event_distribution": [
                {"hook.event": ev, "claude.hooks.runs": cnt}
                for ev, cnt in self.event_distribution()
            ],
        }

    # ── Span export ───────────────────────────────────────────────────────────

    def _has_session_column(self) -> bool:
        """Check whether hook_metrics has the session column (added in Phase 1)."""
        if hasattr(self, "_session_col_cached"):
            return self._session_col_cached
        rows = self._query("PRAGMA table_info(hook_metrics)")
        self._session_col_cached = any(r[1] == "session" for r in rows)
        return self._session_col_cached

    def _has_stderr_snippet_column(self) -> bool:
        """Check whether hook_metrics has the stderr_snippet column."""
        if hasattr(self, "_stderr_snippet_col_cached"):
            return self._stderr_snippet_col_cached
        rows = self._query("PRAGMA table_info(hook_metrics)")
        self._stderr_snippet_col_cached = any(r[1] == "stderr_snippet" for r in rows)
        return self._stderr_snippet_col_cached

    def spans_raw(self, hours: int = 24, limit: int = 10000) -> list[tuple]:
        """Return hook_metrics rows for span export.

        Gracefully degrades on old DBs without the session or stderr_snippet column.
        Row order: id, ts, hook, step, cmd, exit_code, duration_ms,
                   real_s, user_s, sys_s, branch, sha, host, repo, session, stderr_snippet
        """
        has_session = self._has_session_column()
        has_stderr = self._has_stderr_snippet_column()
        col_sel = (
            "id, ts, hook, step, cmd, exit_code, duration_ms, "
            "real_s, user_s, sys_s, branch, sha, host, repo"
            + (", session" if has_session else "")
            + (", stderr_snippet" if has_stderr else "")
        )
        rows = self._query(
            f"SELECT {col_sel} FROM hook_metrics "
            "WHERE ts > datetime('now', ?) ORDER BY ts LIMIT ?",
            (f"-{hours} hours", limit),
        )
        if not has_session:
            rows = [r + ("",) for r in rows]
        if not has_stderr:
            rows = [r + ("",) for r in rows]
        return rows

    def audit_spans_raw(self, hours: int = 24, limit: int = 10000) -> list[tuple]:
        """Return audit_events rows for span export.

        Truncates input to 4KB in the query — production rows can reach 114KB.
        Row order: id, ts, session, tool, input
        """
        return self._query(
            "SELECT id, ts, session, tool, substr(input, 1, 4096) "
            "FROM audit_events "
            "WHERE ts > datetime('now', ?) ORDER BY ts LIMIT ?",
            (f"-{hours} hours", limit),
        )
