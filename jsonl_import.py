"""Import hooktime JSONL entries into hooks.db (SQLite).

Reads ~/.claude/hook-metrics.log, deduplicates against existing rows,
and outputs SQL INSERT statements to stdout for the caller to execute.

Prints summary to stderr. Prints "imported|skipped|errors|total" to stdout
as the first line, followed by SQL statements (if not dry-run).
"""

import json
import sqlite3
import sys


def main():
    jsonl_path = sys.argv[1]
    db_path = sys.argv[2]
    dry_run = sys.argv[3] == "1"

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA busy_timeout=1000")

    # Load existing keys for dedup
    existing = set()
    for row in conn.execute("SELECT ts, hook, step FROM hook_metrics"):
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

    conn.close()

    total = imported + skipped + errors

    if dry_run:
        print(f"jsonl-import [DRY RUN]: {total} lines, {imported} would import, {skipped} already exist, {errors} parse errors", file=sys.stderr)
        print(f"{imported}|{skipped}|{errors}|{total}")
        return

    if imported == 0:
        print(f"jsonl-import: {total} lines, 0 new rows (all {skipped} already exist)", file=sys.stderr)
        print(f"0|{skipped}|{errors}|{total}")
        return

    # Output counts line, then SQL statements
    print(f"{imported}|{skipped}|{errors}|{total}")

    def sql_escape(v):
        if isinstance(v, str):
            return "'" + v.replace("'", "''") + "'"
        return str(v)

    cols = "ts, hook, step, cmd, exit_code, duration_ms, real_s, user_s, sys_s, branch, sha, host, repo, session, stderr_snippet"
    for r in rows:
        vals = ", ".join(sql_escape(v) for v in r)
        print(f"INSERT INTO hook_metrics ({cols}) VALUES ({vals});")


if __name__ == "__main__":
    main()
