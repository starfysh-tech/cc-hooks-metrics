import sqlite3
from tests.conftest import seed_hook_metrics


def test_hot_sequences_returns_correlated_failures(test_db_path, db):
    """Steps that fail together in the same session should appear as hot sequences."""
    seed_hook_metrics(test_db_path, [
        # 5 sessions: step-a then step-b, both failing — satisfies MIN_RUNS_FOR_TREND=5
        ("PreToolUse", "step-a", 100, 1, "repo1", "sess-1"),
        ("PreToolUse", "step-b", 200, 1, "repo1", "sess-1"),
        ("PreToolUse", "step-a", 100, 1, "repo1", "sess-2"),
        ("PreToolUse", "step-b", 200, 1, "repo1", "sess-2"),
        ("PreToolUse", "step-a", 100, 1, "repo1", "sess-3"),
        ("PreToolUse", "step-b", 200, 1, "repo1", "sess-3"),
        ("PreToolUse", "step-a", 100, 1, "repo1", "sess-4"),
        ("PreToolUse", "step-b", 200, 1, "repo1", "sess-4"),
        ("PreToolUse", "step-a", 100, 1, "repo1", "sess-5"),
        ("PreToolUse", "step-b", 200, 1, "repo1", "sess-5"),
    ])

    seqs = db.hot_sequences(days=7)
    assert len(seqs) >= 1
    top = seqs[0]
    assert top.prev_step == "step-a"
    assert top.step == "step-b"
    assert top.failures >= 3
    assert top.fail_rate > 0


def test_hot_sequences_empty_db(db):
    """Empty DB returns empty list, no crash."""
    assert db.hot_sequences(days=7) == []


def test_hot_sequences_no_session_column(test_db_path):
    """DB without session column returns empty list."""
    conn = sqlite3.connect(test_db_path)
    conn.execute("DROP TABLE hook_metrics")
    conn.execute("""
        CREATE TABLE hook_metrics (
            id INTEGER PRIMARY KEY, ts TEXT DEFAULT (datetime('now')),
            event TEXT, step TEXT, duration_ms INTEGER,
            exit_code INTEGER, repo TEXT DEFAULT '', cmd TEXT DEFAULT ''
        )
    """)
    conn.commit()
    conn.close()

    from hooks_report.db import HooksDB
    db = HooksDB(test_db_path)
    assert db.hot_sequences(days=7) == []
    db.close()
