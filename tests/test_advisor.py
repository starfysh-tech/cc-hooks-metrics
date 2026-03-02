# tests/test_advisor.py
import sqlite3
from tests.conftest import seed_hook_metrics


def test_guardrail_tuning_async_suggestion(test_db_path, db):
    """High fail rate + slow avg → 'async' suggestion."""
    seed_hook_metrics(test_db_path, [
        ("PreToolUse", "slow-fail", 3000, 1, "repo1", "s1"),
        ("PreToolUse", "slow-fail", 2500, 1, "repo1", "s2"),
        ("PreToolUse", "slow-fail", 2800, 0, "repo1", "s3"),
        ("PreToolUse", "slow-fail", 3100, 1, "repo1", "s4"),
        ("PreToolUse", "slow-fail", 2700, 1, "repo1", "s5"),
    ])

    from hooks_report.advisor import guardrail_tuning
    suggestions = guardrail_tuning(db, days=7)
    assert len(suggestions) >= 1
    s = next(s for s in suggestions if s.step == "slow-fail")
    assert s.category == "async"


def test_guardrail_tuning_investigate_suggestion(test_db_path, db):
    """High fail rate + fast avg → 'investigate' suggestion."""
    seed_hook_metrics(test_db_path, [
        ("PreToolUse", "fast-fail", 100, 1, "repo1", "s1"),
        ("PreToolUse", "fast-fail", 150, 1, "repo1", "s2"),
        ("PreToolUse", "fast-fail", 120, 0, "repo1", "s3"),
        ("PreToolUse", "fast-fail", 80, 1, "repo1", "s4"),
        ("PreToolUse", "fast-fail", 90, 1, "repo1", "s5"),
    ])

    from hooks_report.advisor import guardrail_tuning
    suggestions = guardrail_tuning(db, days=7)
    s = next(s for s in suggestions if s.step == "fast-fail")
    assert s.category == "investigate"


def test_guardrail_tuning_optimize_suggestion(test_db_path, db):
    """Low fail rate + very slow → 'optimize' suggestion."""
    seed_hook_metrics(test_db_path, [
        ("PreToolUse", "heavy", 8000, 0, "repo1", "s1"),
        ("PreToolUse", "heavy", 7500, 0, "repo1", "s2"),
        ("PreToolUse", "heavy", 9000, 0, "repo1", "s3"),
        ("PreToolUse", "heavy", 6000, 0, "repo1", "s4"),
        ("PreToolUse", "heavy", 8500, 0, "repo1", "s5"),
    ])

    from hooks_report.advisor import guardrail_tuning
    suggestions = guardrail_tuning(db, days=7)
    s = next(s for s in suggestions if s.step == "heavy")
    assert s.category == "optimize"


def test_guardrail_tuning_zero_fail_rate_not_misclassified(test_db_path, db):
    """Steps with fail_rate=0.0 should NOT trigger any suggestion (truthiness bug guard)."""
    seed_hook_metrics(test_db_path, [
        ("PreToolUse", "good-step", 200, 0, "repo1", "s1"),
        ("PreToolUse", "good-step", 250, 0, "repo1", "s2"),
        ("PreToolUse", "good-step", 180, 0, "repo1", "s3"),
        ("PreToolUse", "good-step", 220, 0, "repo1", "s4"),
        ("PreToolUse", "good-step", 230, 0, "repo1", "s5"),
    ])

    from hooks_report.advisor import guardrail_tuning
    suggestions = guardrail_tuning(db, days=7)
    step_names = [s.step for s in suggestions]
    assert "good-step" not in step_names


def test_guardrail_tuning_empty_db(db):
    """Empty DB returns empty suggestions, no crash."""
    from hooks_report.advisor import guardrail_tuning
    assert guardrail_tuning(db, days=7) == []


def test_periodic_summary_structure(test_db_path, db):
    """periodic_summary returns a PeriodSummary with expected fields."""
    seed_hook_metrics(test_db_path, [
        ("PreToolUse", "lint", 500, 0, "repo1", "s1"),
        ("PreToolUse", "lint", 300, 1, "repo1", "s2"),
        ("PreToolUse", "test", 200, 0, "repo2", "s3"),
    ])

    from hooks_report.advisor import periodic_summary
    summary = periodic_summary(db, period="weekly")
    assert summary.period == "weekly"
    assert summary.schema == "claude.hooks.summary/v1"
    assert summary.metrics.total_runs == 3
    assert summary.metrics.failures == 1


def test_periodic_summary_empty_db(db):
    """Empty DB produces a summary with zero metrics."""
    from hooks_report.advisor import periodic_summary
    summary = periodic_summary(db, period="daily")
    assert summary.metrics.total_runs == 0


def test_summary_to_json_schema(test_db_path, db):
    """summary_to_json output has required top-level keys."""
    seed_hook_metrics(test_db_path, [
        ("PreToolUse", "lint", 500, 0, "repo1", "s1"),
    ])

    from hooks_report.advisor import periodic_summary, summary_to_json
    summary = periodic_summary(db, period="weekly")
    data = summary_to_json(summary)
    assert data["schema"] == "claude.hooks.summary/v1"
    assert "metrics" in data
    assert "suggestions" in data
    assert "worst_step" in data
