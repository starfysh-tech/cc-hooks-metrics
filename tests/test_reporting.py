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


def test_guardrail_summary_empty_steps(db, monkeypatch):
    """Empty GUARDRAIL_STEPS should return [], not blow up with invalid SQL."""
    import hooks_report.config as config
    monkeypatch.setattr(config, "GUARDRAIL_STEPS", set())
    rows = db.guardrail_summary()
    assert rows == []


def test_guardrail_summary_zero_blocks(db, test_db_path):
    seed_hook_metrics(test_db_path, [
        ("PreToolUse", "guard-security", 50, 0, "repo1", "s1"),
    ])
    rows = db.guardrail_summary()
    assert len(rows) == 1
    assert rows[0].blocks == 0
    assert rows[0].block_rate == 0.0


def test_export_data_includes_guardrails(db, test_db_path):
    seed_hook_metrics(test_db_path, [
        ("PreToolUse", "guard-security", 50, 2, "repo1", "s1"),
        ("PostToolUse", "audit-logger", 100, 0, "repo1", "s1"),
    ])
    data = db.export_data()
    assert "guardrails" in data
    assert "event_distribution" in data
    assert len(data["guardrails"]) == 1
    assert data["guardrails"][0]["hook.step"] == "guard-security"
