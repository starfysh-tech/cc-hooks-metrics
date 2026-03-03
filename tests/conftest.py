import os
import sqlite3
import tempfile
import pytest


@pytest.fixture
def test_db_path():
    """Create a temporary SQLite DB with hook_metrics + audit_events schema."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE hook_metrics (
            id INTEGER PRIMARY KEY,
            ts TEXT DEFAULT (datetime('now')),
            hook TEXT,
            step TEXT,
            duration_ms INTEGER,
            exit_code INTEGER,
            repo TEXT DEFAULT '',
            cmd TEXT DEFAULT '',
            session TEXT DEFAULT ''
        );
        CREATE TABLE audit_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL DEFAULT (datetime('now')),
            session TEXT NOT NULL DEFAULT '',
            tool TEXT NOT NULL DEFAULT '',
            input TEXT NOT NULL DEFAULT ''
        );
    """)
    conn.close()
    yield path
    os.unlink(path)


@pytest.fixture
def db(test_db_path):
    """Return a HooksDB instance backed by the test DB."""
    from hooks_report.db import HooksDB
    hdb = HooksDB(test_db_path)
    yield hdb
    hdb.close()


def seed_hook_metrics(db_path, rows):
    """Insert rows into hook_metrics. Each row: (hook, step, duration_ms, exit_code, repo, session)."""
    conn = sqlite3.connect(db_path)
    for r in rows:
        conn.execute(
            "INSERT INTO hook_metrics (hook, step, duration_ms, exit_code, repo, session) VALUES (?,?,?,?,?,?)",
            r,
        )
    conn.commit()
    conn.close()
