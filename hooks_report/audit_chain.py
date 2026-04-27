"""Atomic audit-event chain insert and verifier.

Insert side: single sqlite3 connection with BEGIN IMMEDIATE so concurrent
writers serialize at the SQLite writer-lock level. Hash determinism is
guaranteed because we hash the exact column values being inserted.

Verifier side: walks the table by `id` ascending, recomputes each row's hash
from persisted column values, reports the first divergence.

Hash format: sha256(NUL-joined: prev_hash, ts, session, tool, input).
Genesis row has prev_hash = '' (empty string).
Sentinel `CHAIN_BREAK_<ts>` is inserted as prev_hash when a write-side
read finds a non-empty table whose tail row_hash is empty (pre-migration
data, or prior insert failure).
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sqlite3
import sys
from collections.abc import Iterable

from . import config


def _digest(prev_hash: str, ts: str, session: str, tool: str, input_payload: str) -> str:
    """sha256 of NUL-joined fields. NUL is not valid in any of the inputs after
    the bash-side stripping, so collisions via field-boundary ambiguity are
    not a practical concern."""
    parts: Iterable[bytes] = (s.encode() for s in (prev_hash, ts, session, tool, input_payload))
    return hashlib.sha256(b"\x00".join(parts)).hexdigest()


def insert_chained(
    db_path: str, ts: str, session: str, tool: str, input_payload: str
) -> tuple[bool, str]:
    """Atomically insert a chained audit_events row.

    Returns (ok, message). On failure the row is not inserted; caller may
    fall back to a non-chained insert if it chooses.
    """
    try:
        with sqlite3.connect(db_path, timeout=2.0) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT COALESCE(row_hash, '') FROM audit_events ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if row is None:
                prev_hash = ""
            elif row[0]:
                prev_hash = row[0]
            else:
                # Sentinel disambiguates a legacy/pre-migration tail with empty
                # row_hash from a legitimate genesis row (also empty by convention).
                prev_hash = f"CHAIN_BREAK_{ts}"
                print(
                    f"warn: audit-chain: tail row_hash empty; inserted boundary sentinel {prev_hash!r}",
                    file=sys.stderr,
                )
            row_hash = _digest(prev_hash, ts, session, tool, input_payload)
            conn.execute(
                "INSERT INTO audit_events (ts, session, tool, input, prev_hash, row_hash) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (ts, session, tool, input_payload, prev_hash, row_hash),
            )
            conn.commit()
            return True, row_hash
    except sqlite3.Error as e:
        return False, f"sqlite error: {e}"


# ── Verifier ─────────────────────────────────────────────────────────────────


def _has_chain_columns(db_path: str) -> bool:
    """Return True if audit_events has both prev_hash and row_hash columns."""
    with sqlite3.connect(db_path, timeout=2.0) as conn:
        cur = conn.execute("PRAGMA table_info(audit_events)")
        cols = {row[1] for row in cur.fetchall()}
    return {"prev_hash", "row_hash"}.issubset(cols)


def verify_chain(db_path: str) -> tuple[int, list[str]]:
    """Walk audit_events ordered by id, recomputing each row's hash.

    Returns (rc, messages). rc is:
        0 — chain valid, or table empty, or pre-migration schema (note returned)
        1 — at least one chain break
    """
    if not os.path.exists(db_path):
        return 0, [f"audit-chain: db not present at {db_path}"]

    if not _has_chain_columns(db_path):
        return 0, ["audit-chain: pre-migration schema (no row_hash column) — nothing to verify"]

    breaks: list[str] = []
    rows_seen = 0
    expected_prev = ""

    with sqlite3.connect(db_path, timeout=2.0) as conn:
        cur = conn.execute(
            "SELECT id, ts, session, tool, input, "
            "COALESCE(prev_hash,''), COALESCE(row_hash,'') "
            "FROM audit_events ORDER BY id ASC"
        )
        for row in cur:
            rows_seen += 1
            rid, ts, session, tool, input_payload, prev_hash, row_hash = row
            is_sentinel = prev_hash.startswith("CHAIN_BREAK_")
            if is_sentinel:
                # Boundary marker — surface it but still verify the digest so a
                # tampered sentinel row is detected before becoming the next anchor.
                breaks.append(
                    f"row id={rid}: chain reset at sentinel {prev_hash!r}"
                )
            elif prev_hash != expected_prev:
                breaks.append(
                    f"row id={rid}: prev_hash mismatch — expected {expected_prev!r}, got {prev_hash!r}"
                )
            recomputed = _digest(prev_hash, ts, session, tool, input_payload)
            if recomputed != row_hash:
                breaks.append(
                    f"row id={rid}: row_hash mismatch — expected {recomputed!r}, got {row_hash!r}"
                )
            expected_prev = row_hash

    msgs: list[str] = []
    if breaks:
        msgs.append(f"audit-chain: {len(breaks)} issue(s) across {rows_seen} row(s):")
        msgs.extend(f"  {b}" for b in breaks)
        return 1, msgs
    msgs.append(f"audit-chain: OK — {rows_seen} row(s) verified")
    return 0, msgs


def main(argv: list[str] | None = None) -> int:
    """Standalone CLI: insert or verify audit chain rows.

    Verify:  python -m hooks_report.audit_chain --verify [--db PATH]
    Insert:  python -m hooks_report.audit_chain --insert TS SESSION TOOL INPUT
    """
    parser = argparse.ArgumentParser(prog="audit-chain", description="audit-event chain insert/verify")
    parser.add_argument("--verify", action="store_true", help="Verify chain integrity")
    parser.add_argument(
        "--insert",
        nargs=4,
        metavar=("TS", "SESSION", "TOOL", "INPUT"),
        help="Atomically insert one chained row from the four positional values",
    )
    parser.add_argument(
        "--db",
        default=os.environ.get("CLAUDE_HOOKS_DB", config.DEFAULT_DB_PATH),
        help="Path to hooks.db",
    )
    args = parser.parse_args(argv)

    if args.insert:
        ts, session, tool, payload = args.insert
        ok, msg = insert_chained(args.db, ts, session, tool, payload)
        if not ok:
            print(f"warn: audit-chain: insert failed: {msg}", file=sys.stderr)
            return 1
        return 0

    if args.verify:
        rc, messages = verify_chain(args.db)
        for m in messages:
            print(m, file=sys.stderr if rc != 0 else sys.stdout)
        return rc

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
