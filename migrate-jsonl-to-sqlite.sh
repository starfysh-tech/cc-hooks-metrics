#!/usr/bin/env bash
# One-time migration: imports existing JSONL files into SQLite
set -uo pipefail

SCRIPT_DIR="$(dirname "$0")"
source "$SCRIPT_DIR/db-init.sh"
_init_hooks_db

echo "Migrating to: $HOOKS_DB"

# ── audit_events ──────────────────────────────────────────────────────────────
audit_files=()
for f in "$HOME/.claude/audit.jsonl" "$HOME/.claude/audit.jsonl".*.old; do
  [ -f "$f" ] && audit_files+=("$f")
done

if [ ${#audit_files[@]} -gt 0 ]; then
  echo "Importing ${#audit_files[@]} audit file(s) ($(wc -l "${audit_files[@]}" 2>/dev/null | tail -1 | awk '{print $1}') lines)..."
  tmp_sql=$(mktemp)
  {
    echo "PRAGMA busy_timeout=1000;"
    echo "BEGIN TRANSACTION;"
    # jq outputs SQL INSERT per line; gsub escapes single quotes for SQL
    cat "${audit_files[@]}" | jq -r '
      select(type == "object") |
      "INSERT OR IGNORE INTO audit_events (ts, session, tool, input) VALUES (\u0027" +
      (.ts      // "" | gsub("\u0027"; "\u0027\u0027")) + "\u0027,\u0027" +
      (.session // "" | gsub("\u0027"; "\u0027\u0027")) + "\u0027,\u0027" +
      (.tool    // "" | gsub("\u0027"; "\u0027\u0027")) + "\u0027,\u0027" +
      (.input   // {} | tojson | gsub("\u0027"; "\u0027\u0027")) + "\u0027);"
    ' 2>/dev/null
    echo "COMMIT;"
  } > "$tmp_sql"
  sqlite3 "$HOOKS_DB" >/dev/null < "$tmp_sql"
  audit_count=$(grep -c "^INSERT" "$tmp_sql" || echo 0)
  rm -f "$tmp_sql"
  echo "Imported $audit_count audit_events rows."
else
  echo "No audit files found."
fi

# ── hook_metrics ──────────────────────────────────────────────────────────────
metrics_files=()
for f in "$HOME/.claude/hook-metrics.log" "$HOME/.claude/hook-metrics.log".*.old; do
  [ -f "$f" ] && metrics_files+=("$f")
done

if [ ${#metrics_files[@]} -gt 0 ]; then
  echo "Importing ${#metrics_files[@]} metrics file(s)..."
  tmp_sql=$(mktemp)
  {
    echo "PRAGMA busy_timeout=1000;"
    echo "BEGIN TRANSACTION;"
    cat "${metrics_files[@]}" | grep '^{' | jq -r '
      select(type == "object") |
      "INSERT OR IGNORE INTO hook_metrics (ts, hook, step, cmd, exit_code, duration_ms, real_s, user_s, sys_s, branch, sha, host) VALUES (\u0027" +
      (.ts         // "" | gsub("\u0027"; "\u0027\u0027")) + "\u0027,\u0027" +
      (.hook // .source // "" | gsub("\u0027"; "\u0027\u0027")) + "\u0027,\u0027" +
      (.step       // "" | gsub("\u0027"; "\u0027\u0027")) + "\u0027,\u0027" +
      (.cmd        // "" | gsub("\u0027"; "\u0027\u0027")) + "\u0027," +
      (.exit_code  // 0 | tostring) + "," +
      (.duration_ms // 0 | tostring) + "," +
      (.real_s     // 0 | tostring) + "," +
      (.user_s     // 0 | tostring) + "," +
      (.sys_s      // 0 | tostring) + ",\u0027" +
      (.branch     // "" | gsub("\u0027"; "\u0027\u0027")) + "\u0027,\u0027" +
      (.sha        // "" | gsub("\u0027"; "\u0027\u0027")) + "\u0027,\u0027" +
      (.host       // "" | gsub("\u0027"; "\u0027\u0027")) + "\u0027);"
    ' 2>/dev/null
    echo "COMMIT;"
  } > "$tmp_sql"
  sqlite3 "$HOOKS_DB" >/dev/null < "$tmp_sql"
  metrics_count=$(grep -c "^INSERT" "$tmp_sql" || echo 0)
  rm -f "$tmp_sql"
  echo "Imported $metrics_count hook_metrics rows."
else
  echo "No metrics files found."
fi

echo ""
sqlite3 "$HOOKS_DB" 'SELECT "audit_events: " || COUNT(*) FROM audit_events;
                     SELECT "hook_metrics: " || COUNT(*) FROM hook_metrics;'
echo ""
echo "Clean up originals (~40MB):"
echo "  rm -f ~/.claude/audit.jsonl* ~/.claude/hook-metrics.log*"
