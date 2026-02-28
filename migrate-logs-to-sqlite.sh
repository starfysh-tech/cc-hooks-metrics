#!/usr/bin/env bash
# Full re-import of all hook-metrics log files (handles plaintext and JSON formats).
# Deletes existing hook_metrics rows first to avoid duplicates, then re-imports all.
#
# Plaintext format (Feb 3-4):   2026-02-03T01:29:15Z step-name  real=1.49s user=1.00s sys=0.34s
# JSON format    (Feb 19+):     {"ts":"...","hook":"...","step":"...",...}
# Mixed files contain both formats (early transition period).
set -uo pipefail

SCRIPT_DIR="$(dirname "$0")"
source "$SCRIPT_DIR/db-init.sh"
_init_hooks_db

echo "Migrating hook_metrics to: $HOOKS_DB"

metrics_files=()
for f in "$HOME/.claude/hook-metrics.log" "$HOME/.claude/hook-metrics.log".*.old; do
  [ -f "$f" ] && metrics_files+=("$f")
done

if [ ${#metrics_files[@]} -eq 0 ]; then
  echo "No metrics files found."
  exit 0
fi

total_lines=$(wc -l "${metrics_files[@]}" 2>/dev/null | tail -1 | awk '{print $1}')
echo "Processing ${#metrics_files[@]} file(s) ($total_lines lines)..."

tmp_sql=$(mktemp)
{
  echo "PRAGMA busy_timeout=1000;"
  echo "BEGIN TRANSACTION;"
  echo "DELETE FROM hook_metrics;"

  # Plaintext lines: "2026-02-03T01:29:15Z step-name  real=1.49s user=1.00s sys=0.34s"
  # Skip JSON lines (start with '{') to avoid double-counting in mixed files.
  cat "${metrics_files[@]}" | awk '/^[0-9]/ && !/^{/ {
    ts=$1; step=$2;
    real=0; user_v=0; sys_v=0;
    for(i=3;i<=NF;i++){
      if($i~/^real=/){v=substr($i,6);sub(/s$/,"",v);real=v+0}
      if($i~/^user=/){v=substr($i,6);sub(/s$/,"",v);user_v=v+0}
      if($i~/^sys=/){v=substr($i,5);sub(/s$/,"",v);sys_v=v+0}
    }
    dur=int(real*1000);
    printf "INSERT OR IGNORE INTO hook_metrics (ts, hook, step, cmd, exit_code, duration_ms, real_s, user_s, sys_s, branch, sha, host) VALUES (\047%s\047,\047\047,\047%s\047,\047\047,0,%d,%.3f,%.3f,%.3f,\047\047,\047\047,\047\047);\n", ts, step, dur, real, user_v, sys_v
  }'

  # JSON lines only (grep filters plaintext lines, fixes mixed-format files).
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
imported_count=$(grep -c "^INSERT" "$tmp_sql" || echo 0)
rm -f "$tmp_sql"
echo "Generated $imported_count INSERT statements."

echo ""
sqlite3 "$HOOKS_DB" 'SELECT "hook_metrics: " || COUNT(*) FROM hook_metrics;
                     SELECT "  date range: " || MIN(ts) || " to " || MAX(ts) FROM hook_metrics;'
echo ""
echo "Run 'sqlite3 ~/.claude/hooks.db VACUUM;' to reclaim space after import."
