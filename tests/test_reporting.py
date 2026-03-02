import sqlite3
from tests.conftest import seed_hook_metrics


def test_guardrail_summary_with_blocks(db, test_db_path):
    seed_hook_metrics(test_db_path, [
        ("PreToolUse", "guard-security", 50, 0, "repo1", "s1"),
        ("PreToolUse", "guard-security", 60, 2, "repo1", "s1"),
        ("PreToolUse", "guard-security", 55, 2, "repo1", "s1"),
        ("PostToolUse", "guard-python-lint", 200, 0, "repo1", "s1"),
        ("PostToolUse", "guard-python-lint", 180, 2, "repo1", "s1"),
    ])
    rows = db.guardrail_summary()
    assert len(rows) == 2
    sec = next(r for r in rows if r.step == "guard-security")
    assert sec.total_runs == 3
    assert sec.blocks == 2
    lint = next(r for r in rows if r.step == "guard-python-lint")
    assert lint.total_runs == 2
    assert lint.blocks == 1


def test_guardrail_summary_empty(db):
    rows = db.guardrail_summary()
    assert rows == []


def test_event_distribution(db, test_db_path):
    seed_hook_metrics(test_db_path, [
        ("PostToolUse", "audit-logger", 100, 0, "repo1", "s1"),
        ("PostToolUse", "audit-logger", 100, 0, "repo1", "s1"),
        ("SessionStart", "event-log", 50, 0, "repo1", "s1"),
        ("PreToolUse", "event-log", 30, 0, "repo1", "s1"),
    ])
    rows = db.event_distribution()
    assert len(rows) == 3
    assert rows[0] == ("PostToolUse", 2)  # highest count first


def test_event_distribution_empty(db):
    rows = db.event_distribution()
    assert rows == []
