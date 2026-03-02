# hooks_report/advisor.py
"""Advisor module: turns hook telemetry into actionable tuning recommendations."""

from dataclasses import dataclass
from typing import Optional

from . import config
from .db import HooksDB


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
