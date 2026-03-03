from datetime import datetime, timedelta

from tests.conftest import seed_hook_metrics, seed_hook_metrics_ext


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


def test_guardrail_exit2_excluded_from_failures(db, test_db_path):
    """Guardrail exit=2 (block) must not count as a reliability failure."""
    import hooks_report.config as cfg
    guard_step = next(iter(cfg.GUARDRAIL_STEPS))
    seed_hook_metrics(test_db_path, [
        # Normal hook — legitimate failure
        ("PostToolUse", "audit-logger", 100, 1, "repo1", "s1"),
        # Guardrail block (exit=2) — should NOT count as failure
        ("PreToolUse", guard_step, 50, 2, "repo1", "s1"),
        # Guardrail pass (exit=0) — not a failure
        ("PreToolUse", guard_step, 40, 0, "repo1", "s1"),
    ])

    health = db.health_24h()
    assert health.failures == 1, f"expected 1 failure, got {health.failures}"

    rel = db.assess()
    assert rel.rel_failures == 1, f"assess: expected 1, got {rel.rel_failures}"

    wow = db.wow_summary()
    assert wow.cur_fail == 1, f"wow_summary: expected 1, got {wow.cur_fail}"

    steps = db.step_reliability()
    guard_row = next((r for r in steps if r.step == guard_step), None)
    assert guard_row is not None
    assert guard_row.failures == 0, f"step_reliability: expected 0 guard failures, got {guard_row.failures}"

    agg = db.period_aggregate()
    assert agg.failures == 1, f"period_aggregate: expected 1, got {agg.failures}"


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


def _ts(days_ago: int) -> str:
    """Return a datetime string N days in the past, for use as explicit ts values."""
    return (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")


def test_broken_hook_red_no_success(db, test_db_path):
    """A step with only exit-127 failures and no successes is red."""
    seed_hook_metrics_ext(test_db_path, [
        {"hook": "PreToolUse", "step": "broken-step", "duration_ms": 10,
         "exit_code": 127, "repo": "r1", "session": "s1", "ts": _ts(1)},
        {"hook": "PreToolUse", "step": "broken-step", "duration_ms": 10,
         "exit_code": 127, "repo": "r1", "session": "s1", "ts": _ts(2)},
    ])
    items = db.action_items()
    broken = [i for i in items if i.category == "BROKEN" and i.step == "broken-step"]
    assert len(broken) == 1
    assert broken[0].severity == "red"


def test_broken_hook_red_still_failing(db, test_db_path):
    """A step whose last failure is more recent than its last success is red."""
    seed_hook_metrics_ext(test_db_path, [
        {"hook": "PreToolUse", "step": "broken-step", "duration_ms": 10,
         "exit_code": 0, "repo": "r1", "session": "s1", "ts": _ts(3)},
        {"hook": "PreToolUse", "step": "broken-step", "duration_ms": 10,
         "exit_code": 127, "repo": "r1", "session": "s1", "ts": _ts(1)},
    ])
    items = db.action_items()
    broken = [i for i in items if i.category == "BROKEN" and i.step == "broken-step"]
    assert len(broken) == 1
    assert broken[0].severity == "red"


def test_broken_hook_yellow_resolved(db, test_db_path):
    """A step whose last success is more recent than its last failure is yellow."""
    seed_hook_metrics_ext(test_db_path, [
        {"hook": "PreToolUse", "step": "broken-step", "duration_ms": 10,
         "exit_code": 127, "repo": "r1", "session": "s1", "ts": _ts(3)},
        {"hook": "PreToolUse", "step": "broken-step", "duration_ms": 10,
         "exit_code": 0, "repo": "r1", "session": "s1", "ts": _ts(1)},
    ])
    items = db.action_items()
    broken = [i for i in items if i.category == "BROKEN" and i.step == "broken-step"]
    assert len(broken) == 1
    assert broken[0].severity == "yellow"
