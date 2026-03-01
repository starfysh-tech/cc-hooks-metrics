#!/usr/bin/env bash
set -euo pipefail

# shellcheck source=db-init.sh
source "$(dirname "$0")/db-init.sh"
_init_hooks_db

RAW="$1"; shift
if [[ "$RAW" == *:* ]]; then
  HOOK_EVENT="${RAW%%:*}"; HOOK_NAME="${RAW#*:}"
else
  HOOK_EVENT="PostToolUse"; HOOK_NAME="$RAW"
fi

# Reconstruct command string for logging
CMD_ARGS="$*"

# Capture stdin to temp file for passthrough
input_file=$(mktemp)
cat > "$input_file"

# Temp file for timing data
time_file=$(mktemp)

# Clean up temp files on exit (handles errors and normal exit)
trap 'rm -f "$input_file" "$time_file"' EXIT

# Run command with timing (-p for parseable output: "real X.XX\nuser X.XX\nsys X.XX")
# Disable set -e so non-zero exit codes don't abort the script before we log them
set +e
/usr/bin/time -p -o "$time_file" "$@" < "$input_file"
exit_code=$?
set -e

# Parse timing data
real=$(grep "^real" "$time_file" | awk '{print $2}')
user=$(grep "^user" "$time_file" | awk '{print $2}')
sys=$(grep "^sys" "$time_file" | awk '{print $2}')

# Compute duration_ms from real seconds
duration_ms=$(awk "BEGIN{printf \"%.0f\", $real * 1000}")

# Session ID — prefer env var set by Claude Code (v2.1.9+), fall back to stdin JSON
SESSION_ID="${CLAUDE_SESSION_ID:-}"
if [ -z "$SESSION_ID" ]; then
  SESSION_ID=$(jq -r '.session_id // ""' "$input_file" 2>/dev/null | tr -d '`$\n\r' || echo "")
fi

# Git context — strip shell-injectable chars before heredoc interpolation
branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null | tr -d '`$\n\r' || echo "")
sha=$(git rev-parse --short HEAD 2>/dev/null || echo "")
repo=$(git rev-parse --show-toplevel 2>/dev/null | tr -d '`$\n\r' || echo "")
host=$(hostname 2>/dev/null | tr -d '`$\n\r' || echo "")

ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

sqlite3 "$HOOKS_DB" >/dev/null <<SQL || true
PRAGMA busy_timeout=1000;
BEGIN IMMEDIATE;
INSERT INTO hook_metrics (ts, hook, step, cmd, exit_code, duration_ms, real_s, user_s, sys_s, branch, sha, host, repo, session)
VALUES (
  '$(_sql_escape "$ts")',
  '$(_sql_escape "$HOOK_EVENT")',
  '$(_sql_escape "$HOOK_NAME")',
  '$(_sql_escape "$CMD_ARGS")',
  $exit_code,
  $duration_ms,
  $real,
  $user,
  $sys,
  '$(_sql_escape "$branch")',
  '$(_sql_escape "$sha")',
  '$(_sql_escape "$host")',
  '$(_sql_escape "$repo")',
  '$(_sql_escape "$SESSION_ID")'
);
COMMIT;
SQL

_maybe_prune_hooks_db

exit $exit_code
