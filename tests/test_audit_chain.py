"""Tests for hooks_report.audit_chain — atomic insert and verifier."""
from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from hooks_report.audit_chain import (
    _digest,
    _has_chain_columns,
    insert_chained,
    verify_chain,
)


# ── helpers ──────────────────────────────────────────────────────────────────


@pytest.fixture
def chain_db_path(tmp_path):
    """Schema with chain columns present (matches db-init.sh new-DB shape)."""
    path = str(tmp_path / "audit_chain.db")
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE audit_events ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "ts TEXT NOT NULL, session TEXT NOT NULL, "
            "tool TEXT NOT NULL, input TEXT NOT NULL, "
            "prev_hash TEXT DEFAULT '', row_hash TEXT DEFAULT '')"
        )
        conn.commit()
    return path


@pytest.fixture
def pre_migration_db_path(tmp_path):
    """Schema WITHOUT chain columns — represents an existing user DB."""
    path = str(tmp_path / "pre_chain.db")
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE audit_events ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, session TEXT, "
            "tool TEXT, input TEXT)"
        )
        conn.commit()
    return path


# ── digest determinism ──────────────────────────────────────────────────────


def test_digest_deterministic():
    a = _digest("", "2026-01-01T00:00:00Z", "sess", "tool", "{}")
    b = _digest("", "2026-01-01T00:00:00Z", "sess", "tool", "{}")
    assert a == b
    assert len(a) == 64  # sha256 hex


def test_digest_changes_with_any_field():
    base = _digest("p", "t", "s", "tool", "i")
    assert base != _digest("p2", "t", "s", "tool", "i")
    assert base != _digest("p", "t2", "s", "tool", "i")
    assert base != _digest("p", "t", "s2", "tool", "i")
    assert base != _digest("p", "t", "s", "tool2", "i")
    assert base != _digest("p", "t", "s", "tool", "i2")


# ── insert ──────────────────────────────────────────────────────────────────


def test_insert_genesis_row(chain_db_path):
    ok, row_hash = insert_chained(chain_db_path, "ts1", "sess", "Bash", '{"cmd":"ls"}')
    assert ok
    assert len(row_hash) == 64
    with sqlite3.connect(chain_db_path) as conn:
        row = conn.execute("SELECT prev_hash, row_hash FROM audit_events").fetchone()
    assert row[0] == ""  # genesis prev_hash
    assert row[1] == row_hash


def test_insert_chains_subsequent_rows(chain_db_path):
    ok1, h1 = insert_chained(chain_db_path, "ts1", "sess", "Bash", "a")
    ok2, h2 = insert_chained(chain_db_path, "ts2", "sess", "Bash", "b")
    assert ok1 and ok2
    with sqlite3.connect(chain_db_path) as conn:
        rows = conn.execute(
            "SELECT prev_hash, row_hash FROM audit_events ORDER BY id"
        ).fetchall()
    assert rows[0][0] == ""
    assert rows[1][0] == rows[0][1] == h1
    assert rows[1][1] == h2


def test_concurrent_inserts_serialize(chain_db_path):
    """BEGIN IMMEDIATE serializes writers — no two rows share a prev_hash."""
    barrier = threading.Barrier(8)

    def worker(i: int):
        barrier.wait()  # Maximize contention
        return insert_chained(chain_db_path, f"ts{i}", "sess", "Bash", f"cmd{i}")

    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(worker, range(8)))

    assert all(ok for ok, _ in results)
    with sqlite3.connect(chain_db_path) as conn:
        prevs = [r[0] for r in conn.execute("SELECT prev_hash FROM audit_events ORDER BY id")]
        hashes = [r[0] for r in conn.execute("SELECT row_hash FROM audit_events ORDER BY id")]
    # Genesis is "", remaining must equal the previous row's row_hash exactly.
    assert prevs[0] == ""
    assert prevs[1:] == hashes[:-1]
    # No duplicates → no fork
    assert len(set(hashes)) == len(hashes)


def test_insert_sentinel_when_tail_hash_empty(chain_db_path, capsys):
    """Pre-migration data (legacy row with empty row_hash) → next insert flags break."""
    with sqlite3.connect(chain_db_path) as conn:
        conn.execute(
            "INSERT INTO audit_events (ts, session, tool, input, prev_hash, row_hash) "
            "VALUES ('legacy', 'sess', 'Bash', 'x', '', '')"
        )
        conn.commit()
    ok, _ = insert_chained(chain_db_path, "ts2", "sess", "Bash", "y")
    assert ok
    err = capsys.readouterr().err
    assert "boundary sentinel" in err
    with sqlite3.connect(chain_db_path) as conn:
        row = conn.execute(
            "SELECT prev_hash FROM audit_events WHERE ts='ts2'"
        ).fetchone()
    assert row[0].startswith("CHAIN_BREAK_")


# ── verify ──────────────────────────────────────────────────────────────────


def test_verify_empty_db_ok(chain_db_path):
    rc, msgs = verify_chain(chain_db_path)
    assert rc == 0
    assert any("OK" in m for m in msgs)


def test_verify_genesis_and_chain_ok(chain_db_path):
    insert_chained(chain_db_path, "t1", "s", "Bash", "a")
    insert_chained(chain_db_path, "t2", "s", "Bash", "b")
    insert_chained(chain_db_path, "t3", "s", "Bash", "c")
    rc, msgs = verify_chain(chain_db_path)
    assert rc == 0
    assert any("3 row(s) verified" in m for m in msgs)


def test_verify_detects_input_tamper(chain_db_path):
    insert_chained(chain_db_path, "t1", "s", "Bash", "a")
    insert_chained(chain_db_path, "t2", "s", "Bash", "b")
    with sqlite3.connect(chain_db_path) as conn:
        conn.execute("UPDATE audit_events SET input='TAMPERED' WHERE ts='t1'")
        conn.commit()
    rc, msgs = verify_chain(chain_db_path)
    assert rc == 1
    text = "\n".join(msgs)
    assert "row_hash mismatch" in text


def test_verify_detects_prev_hash_break(chain_db_path):
    insert_chained(chain_db_path, "t1", "s", "Bash", "a")
    insert_chained(chain_db_path, "t2", "s", "Bash", "b")
    with sqlite3.connect(chain_db_path) as conn:
        conn.execute("UPDATE audit_events SET prev_hash='deadbeef' WHERE ts='t2'")
        conn.commit()
    rc, msgs = verify_chain(chain_db_path)
    assert rc == 1
    assert any("prev_hash mismatch" in m for m in msgs)


def test_verify_pre_migration_schema_returns_zero(pre_migration_db_path):
    rc, msgs = verify_chain(pre_migration_db_path)
    assert rc == 0
    assert any("pre-migration" in m for m in msgs)


def test_verify_missing_db_returns_zero(tmp_path):
    rc, msgs = verify_chain(str(tmp_path / "nonexistent.db"))
    assert rc == 0
    assert any("not present" in m for m in msgs)


def test_verify_sentinel_resets_chain(chain_db_path):
    """Sentinel row is reported but downstream rows are still validated."""
    # Genesis row OK
    insert_chained(chain_db_path, "t1", "s", "Bash", "a")
    # Insert a tail row with empty hash (simulates pre-migration legacy row)
    with sqlite3.connect(chain_db_path) as conn:
        conn.execute(
            "INSERT INTO audit_events (ts, session, tool, input, prev_hash, row_hash) "
            "VALUES ('t-legacy', 's', 'Bash', 'x', '', '')"
        )
        conn.commit()
    # Next chained insert flags the boundary
    insert_chained(chain_db_path, "t3", "s", "Bash", "c")
    rc, msgs = verify_chain(chain_db_path)
    # Sentinel boundary is an issue (rc=1) — verifier must surface it.
    assert rc == 1
    assert any("sentinel" in m or "row_hash mismatch" in m for m in msgs)


def test_has_chain_columns_true_on_new_schema(chain_db_path):
    assert _has_chain_columns(chain_db_path) is True


def test_has_chain_columns_false_on_pre_migration(pre_migration_db_path):
    assert _has_chain_columns(pre_migration_db_path) is False


def test_verify_detects_tampered_sentinel_row(chain_db_path):
    """A sentinel row whose payload is mutated must be reported as tampered,
    not silently accepted via the boundary-marker path."""
    insert_chained(chain_db_path, "t1", "s", "Bash", "a")
    with sqlite3.connect(chain_db_path) as conn:
        # Insert a synthetic sentinel row that the verifier should accept...
        from hooks_report.audit_chain import _digest
        ts2, sess2, tool2, payload2 = "t2", "s", "Bash", "b"
        prev_hash = "CHAIN_BREAK_t2"
        rh = _digest(prev_hash, ts2, sess2, tool2, payload2)
        conn.execute(
            "INSERT INTO audit_events (ts, session, tool, input, prev_hash, row_hash) "
            "VALUES (?,?,?,?,?,?)",
            (ts2, sess2, tool2, payload2, prev_hash, rh),
        )
        # ...then tamper with its tool field, leaving row_hash unchanged.
        conn.execute("UPDATE audit_events SET tool='TAMPERED' WHERE ts='t2'")
        conn.commit()
    rc, msgs = verify_chain(chain_db_path)
    assert rc == 1
    text = "\n".join(msgs)
    assert "row_hash mismatch" in text
