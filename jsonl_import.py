"""Import hooktime JSONL entries into hooks.db (SQLite).

Reads ~/.claude/hook-metrics.log, deduplicates against existing rows,
and inserts new rows using parameterized queries.

Prints summary to stderr. Prints "imported|skipped|errors|total" to stdout.
Exit code 0 = success or nothing to do, 1 = validation failure.
"""

import json
import sqlite3
import sys

COLS = (
    "ts", "hook", "step", "cmd", "exit_code", "duration_ms",
    "real_s", "user_s", "sys_s", "branch", "sha", "host",
    "repo", "session", "stderr_snippet",
)
PLACEHOLDERS = ", ".join("?" for _ in COLS)
INSERT_SQL = f"INSERT INTO hook_metrics ({', '.join(COLS)}) VALUES ({PLACEHOLDERS})"


def main():
    jsonl_path = sys.argv[1]
    db_path = sys.argv[2]
    dry_run = sys.argv[3] == "1"

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA busy_timeout=1000")

    # Load existing keys for dedup (last 90 days — older rows are pruned anyway)
    existing = set()
    for row in conn.execute("SELECT ts, hook, step FROM hook_metrics WHERE ts > datetime('now', '-90 days')"):
        existing.add((row[0], row[1], row[2]))

    imported = 0
    skipped = 0
    errors = 0
    rows = []

    with open(jsonl_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                errors += 1
                continue

            ts = d.get("ts", "")
            hook = d.get("hook", "")
            step = d.get("step", "")

            if not ts:
                errors += 1
                continue

            key = (ts, hook, step)
            if key in existing:
                skipped += 1
                continue

            existing.add(key)
            duration_ms = int(d.get("duration_ms", 0))
            rows.append((
                ts, hook, step,
                d.get("cmd", ""),
                int(d.get("exit_code", 0)),
                duration_ms,
                round(duration_ms / 1000, 2),
                0, 0,
                d.get("branch", ""),
                d.get("sha", ""),
                d.get("host", ""),
                d.get("repo", ""),
                "", "",
            ))
            imported += 1

    total = imported + skipped + errors

    if dry_run:
        print(f"jsonl-import [DRY RUN]: {total} lines, {imported} would import, {skipped} already exist, {errors} parse errors", file=sys.stderr)
        print(f"{imported}|{skipped}|{errors}|{total}")
        conn.close()
        return

    if imported == 0:
        print(f"jsonl-import: {total} lines, 0 new rows (all {skipped} already exist)", file=sys.stderr)
        print(f"0|{skipped}|{errors}|{total}")
        conn.close()
        return

    # Insert all rows in a single transaction using parameterized queries
    conn.execute("BEGIN TRANSACTION")
    conn.executemany(INSERT_SQL, rows)
    conn.execute("COMMIT")
    conn.close()

    print(f"{imported}|{skipped}|{errors}|{total}")


if __name__ == "__main__":
    main()
