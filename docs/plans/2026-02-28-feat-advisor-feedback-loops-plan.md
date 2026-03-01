---
title: "Phase 4: Advisor + Feedback Loops"
phase: 4
date: 2026-02-28
dependencies: "Phase 2 (step_reliability query), Phase 1 (session column for hot_sequences)"
status: ready
---

# Phase 4: Advisor + Feedback Loops

## Overview

Add an advisor module that analyzes hook telemetry to generate actionable tuning suggestions and periodic summaries. The advisor identifies misconfigured guardrails (too slow, too noisy, missing timeouts), surfaces hot failure sequences within sessions, and produces privacy-safe periodic summaries exportable as JSON. This turns passive reporting into active recommendations.

## Dependencies

- **Phase 2 `step_reliability()`**: Required for `guardrail_tuning()` — needs per-step p50/p90/p99, fail rate, and pain index data
- **Phase 1 session column**: Required for `hot_sequences()` — groups ordered steps within sessions to find correlated failures. Gated behind `_has_session_column()` check; returns empty list if column missing
- **Phase 2 `step_drilldown()`**: Used by `periodic_summary()` for per-step breakdown stats
- **No new Python dependencies** — uses existing Rich + Textual stack

## Implementation

### 4a: New file `hooks_report/advisor.py`

Create `hooks_report/advisor.py` (~200 lines) with two dataclasses and three core functions.

#### Dataclasses

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from . import config
from .db import HooksDB


@dataclass
class TuningSuggestion:
    category: str       # "async", "investigate", "optimize", "add-timeout"
    step: str           # hook step name
    condition: str      # human-readable condition that triggered this suggestion
    recommendation: str # actionable fix description
    severity: str       # "red" or "yellow"


@dataclass
class PeriodSummary:
    """Privacy-safe aggregate summary — no file paths, session IDs, or repo names."""
    schema: str = "claude.hooks.summary/v1"
    period: str = ""            # "daily" or "weekly"
    start_date: str = ""        # ISO8601
    end_date: str = ""          # ISO8601
    total_runs: int = 0
    total_failures: int = 0
    failure_rate: Optional[float] = None
    total_overhead_ms: int = 0
    unique_steps: int = 0
    unique_repos: int = 0       # count only, no names
    worst_step: str = ""        # step with highest pain index
    worst_pain_index: float = 0.0
    suggestions: list[TuningSuggestion] = field(default_factory=list)
```

#### Config additions in `hooks_report/config.py`

```python
# Advisor tuning thresholds — initial values, calibrate against real data
TUNING_HIGH_FAIL_RATE = 30.0       # % — triggers "async" suggestion
TUNING_HIGH_FAIL_AVG_MS = 2000     # ms — combined with high fail rate
TUNING_NOISY_FAIL_RATE = 20.0      # % — triggers "investigate" suggestion
TUNING_NOISY_MAX_AVG_MS = 500      # ms — cheap but noisy threshold
TUNING_SLOW_MAX_FAIL_RATE = 5.0    # % — triggers "optimize" suggestion
TUNING_SLOW_MIN_AVG_MS = 5000      # ms — low fail but slow
TUNING_MISSING_TIMEOUT_P99_MS = 10000  # ms — no timeout + p99 > this
HOT_SEQUENCE_FAIL_RATE = 20.0      # % — sequence failure threshold
SUMMARY_PERIODS = {"daily": 1, "weekly": 7}  # period name -> days
```

#### `guardrail_tuning()` function

```python
def guardrail_tuning(db: HooksDB, days: int = 7) -> list[TuningSuggestion]:
    """Analyze step reliability data and return tuning suggestions.

    Condition table (evaluated in order, first match wins per step):

    | Condition                          | Category     | Severity |
    |------------------------------------|-------------|----------|
    | >30% fail + >2000ms avg            | async       | red      |
    | >20% fail + <500ms avg             | investigate | yellow   |
    | <5% fail + >5000ms avg             | optimize    | yellow   |
    | No timeout configured + p99 >10s   | add-timeout | red      |

    All thresholds are configurable in config.py. These are initial guesses
    that require calibration against real data.
    """
    suggestions: list[TuningSuggestion] = []
    steps = db.step_reliability(days=days)  # Phase 2 dependency

    for s in steps:
        # Skip steps with insufficient data
        if s.total_runs < config.MIN_RUNS_FOR_TREND:
            continue

        fail_rate = s.fail_rate or 0.0
        has_timeout = s.step in config.STEP_TIMEOUTS

        # Condition 1: high fail + slow — move to async
        if fail_rate > config.TUNING_HIGH_FAIL_RATE and s.avg_ms > config.TUNING_HIGH_FAIL_AVG_MS:
            suggestions.append(TuningSuggestion(
                category="async",
                step=s.step,
                condition=f"{fail_rate:.1f}% fail rate + {s.avg_ms:.0f}ms avg",
                recommendation="Move to background (async: true) or increase failure tolerance",
                severity="red",
            ))
        # Condition 2: noisy but cheap — investigate config
        elif fail_rate > config.TUNING_NOISY_FAIL_RATE and s.avg_ms < config.TUNING_NOISY_MAX_AVG_MS:
            suggestions.append(TuningSuggestion(
                category="investigate",
                step=s.step,
                condition=f"{fail_rate:.1f}% fail rate but only {s.avg_ms:.0f}ms avg",
                recommendation="Cheap but noisy -- check hook config or error handling",
                severity="yellow",
            ))
        # Condition 3: reliable but slow — optimize
        elif fail_rate < config.TUNING_SLOW_MAX_FAIL_RATE and s.avg_ms > config.TUNING_SLOW_MIN_AVG_MS:
            suggestions.append(TuningSuggestion(
                category="optimize",
                step=s.step,
                condition=f"{fail_rate:.1f}% fail rate but {s.avg_ms:.0f}ms avg",
                recommendation="Low failure but slow -- consider caching or parallelization",
                severity="yellow",
            ))
        # Condition 4: no timeout + extremely slow p99
        if not has_timeout and s.p99_ms > config.TUNING_MISSING_TIMEOUT_P99_MS:
            suggestions.append(TuningSuggestion(
                category="add-timeout",
                step=s.step,
                condition=f"No timeout configured, p99 = {s.p99_ms}ms",
                recommendation=f"Add to STEP_TIMEOUTS in config.py (suggested: {s.p99_ms * 2}ms)",
                severity="red",
            ))

    return suggestions
```

Note: `s.avg_ms`, `s.p99_ms`, `s.fail_rate`, `s.total_runs` reference the `StepReliability` dataclass from Phase 2. The exact field names must match the Phase 2 implementation.

#### `hot_sequences()` function

```python
def hot_sequences(db: HooksDB, days: int = 7) -> list[dict]:
    """Find step sequences within sessions where the same step fails >20%.

    Requires Phase 1 session column. Returns empty list if column missing.

    Returns list of dicts:
        {"sequence": ["step-a", "step-b"], "fail_step": "step-b",
         "occurrences": 12, "fail_rate": 33.3}
    """
    if not db._has_session_column():  # Phase 1 gate
        return []

    # Query sessions with ordered steps, group by (prev_step, cur_step) pairs,
    # calculate failure rate for cur_step when preceded by prev_step
    rows = db._query(f"""
        WITH ordered AS (
            SELECT session, step, exit_code, ts,
                   LAG(step) OVER (PARTITION BY session ORDER BY ts) AS prev_step
            FROM hook_metrics
            WHERE ts > datetime('now', '-{days} days')
              AND session != ''
              AND step NOT IN ({_semantic_exit_placeholders()})
        )
        SELECT prev_step, step,
               COUNT(*) AS total,
               SUM(CASE WHEN exit_code != 0 THEN 1 ELSE 0 END) AS fails,
               ROUND(100.0 * SUM(CASE WHEN exit_code != 0 THEN 1 ELSE 0 END)
                     / NULLIF(COUNT(*), 0), 1) AS fail_rate
        FROM ordered
        WHERE prev_step IS NOT NULL
        GROUP BY prev_step, step
        HAVING total >= {config.MIN_RUNS_FOR_TREND}
           AND fail_rate > {config.HOT_SEQUENCE_FAIL_RATE}
        ORDER BY fail_rate DESC
        LIMIT 10
    """)

    return [
        {
            "sequence": [r[0], r[1]],
            "fail_step": r[1],
            "occurrences": int(r[2]),
            "fail_rate": float(r[4]),
        }
        for r in rows
    ]
```

Note: `_semantic_exit_placeholders()` is imported from `db.py`. This function needs to be accessible — either import it or move it to a shared location. The simplest approach: import from db.py where it already exists.

#### `periodic_summary()` function

```python
def periodic_summary(db: HooksDB, period: str = "weekly") -> PeriodSummary:
    """Generate a privacy-safe aggregate summary for the given period.

    Privacy guarantees:
    - No file paths
    - No session IDs
    - No repo names (only count of unique repos)
    - Only aggregate counts and rates
    """
    days = config.SUMMARY_PERIODS.get(period, 7)

    row = db._query_one(f"""
        SELECT
            COUNT(*) AS total_runs,
            SUM(CASE WHEN exit_code != 0 AND step NOT IN ({_semantic_exit_placeholders()})
                THEN 1 ELSE 0 END) AS failures,
            ROUND(100.0 * SUM(CASE WHEN exit_code != 0 AND step NOT IN ({_semantic_exit_placeholders()})
                THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 1) AS fail_rate,
            COALESCE(SUM(duration_ms), 0) AS overhead_ms,
            COUNT(DISTINCT step) AS unique_steps,
            COUNT(DISTINCT repo) AS unique_repos,
            MIN(ts) AS start_ts,
            MAX(ts) AS end_ts
        FROM hook_metrics
        WHERE ts > datetime('now', '-{days} days')
    """)

    # Find worst step by pain index
    suggestions = guardrail_tuning(db, days=days)
    steps = db.step_reliability(days=days)
    worst = max(steps, key=lambda s: s.pain_index, default=None) if steps else None

    summary = PeriodSummary(
        period=period,
        start_date=row[6] or "",
        end_date=row[7] or "",
        total_runs=int(row[0]),
        total_failures=int(row[1] or 0),
        failure_rate=float(row[2]) if row[2] is not None else None,
        total_overhead_ms=int(row[3]),
        unique_steps=int(row[4]),
        unique_repos=int(row[5]),
        worst_step=worst.step if worst else "",
        worst_pain_index=worst.pain_index if worst else 0.0,
        suggestions=suggestions,
    )
    return summary
```

Note: `s.pain_index` references `StepReliability.pain_index` from Phase 2, defined as `(total_s) * (fail_rate / 100)`.

#### `summary_to_json()` helper

```python
def summary_to_json(summary: PeriodSummary) -> dict:
    """Convert PeriodSummary to JSON-serializable dict."""
    return {
        "schema": summary.schema,
        "period": summary.period,
        "start_date": summary.start_date,
        "end_date": summary.end_date,
        "metrics": {
            "total_runs": summary.total_runs,
            "total_failures": summary.total_failures,
            "failure_rate": summary.failure_rate,
            "total_overhead_ms": summary.total_overhead_ms,
            "unique_steps": summary.unique_steps,
            "unique_repos": summary.unique_repos,
            "worst_step": summary.worst_step,
            "worst_pain_index": summary.worst_pain_index,
        },
        "suggestions": [
            {
                "category": s.category,
                "step": s.step,
                "condition": s.condition,
                "recommendation": s.recommendation,
                "severity": s.severity,
            }
            for s in summary.suggestions
        ],
    }
```

### 4b: AdvisorScreen in `hooks_report/screens.py`

Add `AdvisorScreen` to `screens.py` (created in Phase 3). Follows the same pattern as `DetailScreen` in `tui.py`.

```python
from textual.binding import Binding
from textual.containers import ScrollableContainer
from textual.screen import Screen
from textual.widgets import Footer, Header, Static
from rich.text import Text

from .db import HooksDB
from .advisor import guardrail_tuning, hot_sequences


class AdvisorScreen(Screen):
    """Advisor view: tuning suggestions + hot failure sequences."""

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("q", "app.quit", "Quit"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        with ScrollableContainer():  # Do NOT override CSS
            yield Static(id="advisor-header")
            yield Static(id="advisor-tuning")
            yield Static(id="advisor-sequences")
        yield Footer()

    def on_mount(self) -> None:
        db: HooksDB = self.app.db  # type: ignore[attr-defined]
        self.app.sub_title = "Advisor"

        self.query_one("#advisor-header", Static).update(
            Text("\n  Guardrail Tuning Suggestions", style="bold")
        )

        # Tuning suggestions
        suggestions = guardrail_tuning(db)
        tuning = Text()
        if not suggestions:
            tuning.append("  No tuning suggestions -- all hooks within thresholds.", style="green")
        else:
            for s in suggestions:
                severity_style = "red" if s.severity == "red" else "yellow"
                tuning.append(f"\n  [{s.category.upper()}] ", style=severity_style)
                tuning.append(f"{s.step}\n")
                tuning.append(f"    Condition: {s.condition}\n", style="dim")
                tuning.append(f"    Action: {s.recommendation}\n")
        self.query_one("#advisor-tuning", Static).update(tuning)

        # Hot sequences (Phase 1 gated)
        sequences = hot_sequences(db)
        seq_text = Text("\n  Hot Failure Sequences", style="bold")
        if not sequences:
            seq_text.append("\n  No hot sequences detected.", style="green")
            if not db._has_session_column():
                seq_text.append("\n  (Session column not yet available -- requires Phase 1)", style="dim")
        else:
            for hs in sequences:
                seq_text.append(f"\n  {hs['sequence'][0]} -> {hs['fail_step']}", style="red")
                seq_text.append(f"  {hs['occurrences']} occurrences, {hs['fail_rate']:.1f}% fail rate\n")
        self.query_one("#advisor-sequences", Static).update(seq_text)
```

Key constraints (from MEMORY.md):
- Do NOT override `ScrollableContainer` CSS -- it has correct defaults
- All content must be `rich.text.Text` objects, not markup strings
- Access db via `self.app.db`

### 4c: `section_advisor()` in `hooks_report/static.py`

Add a verbose-only section following the existing `_hdr()` + query + `console.print()` pattern.

```python
def section_advisor(console: Console, db: HooksDB) -> None:
    """Advisor section: tuning suggestions + hot sequences (verbose only)."""
    from .advisor import guardrail_tuning, hot_sequences

    _hdr(console, "Advisor")
    console.print()

    suggestions = guardrail_tuning(db)
    if not suggestions:
        console.print(Text("  No tuning suggestions -- all hooks within thresholds.", style="green"))
    else:
        console.print(Text("  Guardrail Tuning Suggestions:", style="bold"))
        for s in suggestions:
            severity_style = "red" if s.severity == "red" else "yellow"
            line = Text()
            line.append(f"  [{s.category.upper()}] ", style=severity_style)
            line.append(f"{s.step}  ")
            line.append(f"{s.condition}", style="dim")
            console.print(line)
            console.print(Text(f"    -> {s.recommendation}"))

    sequences = hot_sequences(db)
    if sequences:
        console.print()
        console.print(Text("  Hot Failure Sequences:", style="bold"))
        for hs in sequences:
            line = Text()
            line.append(f"  {hs['sequence'][0]} -> {hs['fail_step']}", style="red")
            line.append(f"  ({hs['occurrences']} times, {hs['fail_rate']:.1f}% fail)")
            console.print(line)
```

Call site in `render_static()` — add after `section_projects_compact()` in the verbose block:

```python
if verbose:
    section_perf_compact(console, db, summary)
    section_projects_compact(console, db)
    section_advisor(console, db)  # NEW
```

And in the legacy verbose sections:

```python
if verbose:
    section_health(console, db)
    ...
    section_trends(console, db)
    section_advisor(console, db)  # Also accessible in full verbose
```

### 4d: CLI additions in `hooks_report/cli.py`

```python
parser.add_argument(
    "--summary",
    choices=["daily", "weekly"],
    default=None,
    help="Generate periodic summary (daily or weekly)",
)
parser.add_argument(
    "--export-summary",
    action="store_true",
    help="Export periodic summary as JSON (use with --summary)",
)
```

### 4e: Dispatch in `hooks_report/__main__.py`

Add before the existing `if args.export:` block:

```python
if args.summary:
    from .advisor import periodic_summary, summary_to_json
    summary = periodic_summary(db, period=args.summary)
    if args.export_summary:
        import json
        print(json.dumps(summary_to_json(summary), indent=2))
    else:
        from .static import render_summary
        render_summary(db, summary)
    return
```

This requires a `render_summary()` function in `static.py` that prints the summary in Rich format:

```python
def render_summary(db: HooksDB, summary: PeriodSummary) -> None:
    """Print a periodic summary in Rich format."""
    console = Console()
    _hdr(console, f"{summary.period.title()} Summary ({summary.start_date[:10]} to {summary.end_date[:10]})")
    console.print()
    console.print(f"  Total runs:      {summary.total_runs}")
    console.print(f"  Failures:        {summary.total_failures} ({summary.failure_rate or 0:.1f}%)")
    console.print(f"  Overhead:        {summary.total_overhead_ms / 60000:.1f} min")
    console.print(f"  Unique steps:    {summary.unique_steps}")
    console.print(f"  Unique repos:    {summary.unique_repos}")
    if summary.worst_step:
        console.print(f"  Worst step:      {summary.worst_step} (pain index: {summary.worst_pain_index:.1f})")
    if summary.suggestions:
        console.print()
        console.print(Text("  Suggestions:", style="bold"))
        for s in summary.suggestions:
            severity_style = "red" if s.severity == "red" else "yellow"
            line = Text(f"    [{s.category.upper()}] ", style=severity_style)
            line.append(f"{s.step}: {s.recommendation}")
            console.print(line)
    console.print()
```

### 4f: TUI binding in `hooks_report/tui.py`

Add to `HooksReportApp.BINDINGS`:

```python
Binding("a", "push_screen('advisor')", "Advisor"),
```

Add to `on_mount()`:

```python
from .screens import AdvisorScreen
self.install_screen(AdvisorScreen(), "advisor")
```

## Files Changed

| File | Change | Notes |
|------|--------|-------|
| `hooks_report/advisor.py` | **NEW** (~200 lines) | TuningSuggestion, PeriodSummary dataclasses + guardrail_tuning(), hot_sequences(), periodic_summary(), summary_to_json() |
| `hooks_report/config.py` | +10 lines | Tuning thresholds (TUNING_HIGH_FAIL_RATE, etc.) + SUMMARY_PERIODS |
| `hooks_report/screens.py` | +~45 lines | AdvisorScreen class (Phase 3 creates this file) |
| `hooks_report/static.py` | +~40 lines | section_advisor() + render_summary() |
| `hooks_report/cli.py` | +~10 lines | --summary, --export-summary arguments |
| `hooks_report/__main__.py` | +~10 lines | Summary dispatch branch |
| `hooks_report/tui.py` | +2 lines | Binding + screen install for AdvisorScreen |

## Verification

```bash
# Verify advisor module loads
python3 -c "from hooks_report.advisor import guardrail_tuning, periodic_summary; print('OK')"

# Verify tuning suggestions (requires Phase 2 step_reliability)
~/.claude/hooks/hooks-report.sh --verbose | grep -A 20 "Advisor"

# Generate weekly summary (static)
~/.claude/hooks/hooks-report.sh --summary weekly

# Export weekly summary as JSON
~/.claude/hooks/hooks-report.sh --summary weekly --export-summary | python3 -m json.tool

# Verify JSON schema
~/.claude/hooks/hooks-report.sh --summary weekly --export-summary | python3 -c "
import json, sys
d = json.load(sys.stdin)
assert d['schema'] == 'claude.hooks.summary/v1'
assert 'metrics' in d
assert 'suggestions' in d
print('Schema OK')
"

# TUI: verify advisor screen
~/.claude/hooks/hooks-report.sh  # press 'a' for Advisor

# Verify hot sequences (requires Phase 1 session column)
# If session column exists:
python3 -c "
from hooks_report.db import HooksDB
from hooks_report.advisor import hot_sequences
db = HooksDB()
seqs = hot_sequences(db)
print(f'{len(seqs)} hot sequences found')
"
```

## Risks & Notes

- **Threshold calibration**: All tuning thresholds (`TUNING_HIGH_FAIL_RATE=30.0`, `TUNING_NOISY_FAIL_RATE=20.0`, etc.) are initial guesses. After first deployment, review suggestions against actual data and adjust. Document which thresholds produced false positives.
- **Pain index formula**: `(total_s) * (fail_rate / 100)` is untested. A step running 1000s total with 5% fail rate gets pain index 50.0, while a step running 10s with 50% fail rate gets 5.0. This intentionally weights total time spent, but may need rebalancing.
- **hot_sequences() SQL complexity**: The window function query (LAG + GROUP BY) may be slow on large datasets. The LIMIT 10 + MIN_RUNS_FOR_TREND filters keep result size bounded, but monitor query time on 45K+ row databases.
- **Phase 2 dependency**: `guardrail_tuning()` and `periodic_summary()` will fail if Phase 2's `step_reliability()` is not implemented. Import errors will surface at call time (lazy import in static.py), not at module load.
- **Privacy**: `periodic_summary()` is privacy-safe by design (aggregate counts only). If repo names or file paths are ever added, this guarantee breaks. The `PeriodSummary` dataclass documents this contract.
- **`_has_session_column()` cache**: This Phase 2 helper caches the PRAGMA check result. If the schema is migrated mid-session (unlikely but possible), the cache returns stale data. Acceptable since a process restart clears it.
