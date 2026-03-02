# hooks_report/advisor.py
"""Advisor module: turns hook telemetry into actionable tuning recommendations."""

from dataclasses import dataclass
from typing import Optional

from . import config
from .db import HooksDB, PeriodAggregate


@dataclass
class TuningSuggestion:
    category: str   # async, investigate, optimize, add-timeout
    step: str
    condition: str
    recommendation: str
    severity: str   # red, yellow


def guardrail_tuning(db: HooksDB, days: int = 7) -> list[TuningSuggestion]:
    """Analyze step reliability and suggest guardrail configuration changes.

    Categories (first-match-wins per step):
    - async: high fail rate + slow -> make async or remove
    - investigate: high fail rate + fast -> likely misconfigured
    - optimize: low fail rate + very slow -> optimize or add timeout
    - add-timeout: no configured timeout + high p99 -> add a timeout
    """
    steps = db.step_reliability(days=days)
    suggestions: list[TuningSuggestion] = []

    for s in steps:
        fail_rate = s.fail_rate if s.fail_rate is not None else 0.0
        avg_ms = s.avg_ms if s.avg_ms is not None else 0.0
        p99_ms = s.p99_ms if s.p99_ms is not None else 0.0
        has_timeout = s.step in config.STEP_TIMEOUTS

        # First-match-wins
        if fail_rate >= config.TUNING_HIGH_FAIL_RATE and avg_ms >= config.TUNING_HIGH_FAIL_AVG_MS:
            suggestions.append(TuningSuggestion(
                category="async",
                step=s.step,
                condition=f"{fail_rate:.0f}% fail, {avg_ms:.0f}ms avg",
                recommendation="High failure rate with significant latency — consider making async or removing",
                severity="red",
            ))
        elif fail_rate >= config.TUNING_NOISY_FAIL_RATE and avg_ms <= config.TUNING_NOISY_MAX_AVG_MS:
            suggestions.append(TuningSuggestion(
                category="investigate",
                step=s.step,
                condition=f"{fail_rate:.0f}% fail, {avg_ms:.0f}ms avg",
                recommendation="Fast but noisy — likely misconfigured or overly strict",
                severity="yellow",
            ))
        elif fail_rate <= config.TUNING_SLOW_MAX_FAIL_RATE and avg_ms >= config.TUNING_SLOW_MIN_AVG_MS:
            suggestions.append(TuningSuggestion(
                category="optimize",
                step=s.step,
                condition=f"{fail_rate:.0f}% fail, {avg_ms:.0f}ms avg",
                recommendation="Reliable but slow — optimize implementation or add timeout",
                severity="yellow",
            ))
        elif not has_timeout and p99_ms >= config.TUNING_MISSING_TIMEOUT_P99_MS:
            suggestions.append(TuningSuggestion(
                category="add-timeout",
                step=s.step,
                condition=f"no timeout, p99={p99_ms:.0f}ms",
                recommendation=f"No configured timeout and p99 is {p99_ms:.0f}ms — add a timeout to STEP_TIMEOUTS",
                severity="yellow",
            ))

    return suggestions


@dataclass
class PeriodSummary:
    schema: str
    period: str
    dates: dict  # {"start": str, "end": str}
    metrics: PeriodAggregate
    worst_step: Optional[str]
    worst_pain: Optional[float]
    suggestions: list[TuningSuggestion]


def periodic_summary(db: HooksDB, period: str = "weekly") -> PeriodSummary:
    """Generate a privacy-safe periodic summary (no file paths, session IDs, or repo names)."""
    days = config.SUMMARY_PERIODS.get(period, 7)
    agg = db.period_aggregate(days=days)
    suggestions = guardrail_tuning(db, days=days)

    # Find worst step by pain index
    steps = db.step_reliability(days=days)
    worst_step = None
    worst_pain = None
    if steps:
        worst = max(steps, key=lambda s: s.pain_index if s.pain_index is not None else 0.0)
        if worst.pain_index and worst.pain_index > 0:
            worst_step = worst.step
            worst_pain = worst.pain_index

    return PeriodSummary(
        schema="claude.hooks.summary/v1",
        period=period,
        dates={"start": agg.start_ts, "end": agg.end_ts},
        metrics=agg,
        worst_step=worst_step,
        worst_pain=worst_pain,
        suggestions=suggestions,
    )


def summary_to_json(summary: PeriodSummary) -> dict:
    """Serialize PeriodSummary to JSON-safe dict matching claude.hooks.summary/v1 schema."""
    return {
        "schema": summary.schema,
        "period": summary.period,
        "dates": summary.dates,
        "metrics": {
            "total_runs": summary.metrics.total_runs,
            "failures": summary.metrics.failures,
            "fail_rate": summary.metrics.fail_rate,
            "overhead_ms": summary.metrics.overhead_ms,
            "unique_steps": summary.metrics.unique_steps,
            "unique_repos": summary.metrics.unique_repos,
        },
        "worst_step": summary.worst_step,
        "worst_pain_index": summary.worst_pain,
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
