#!/usr/bin/env bash
set -euo pipefail

# shellcheck source=db-init.sh
source "$(dirname "$0")/db-init.sh"
_init_hooks_db

input=$(cat)
ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
tool=$(echo "$input" | jq -r '.tool_name // "unknown"')
tool_input=$(echo "$input" | jq -c '.tool_input // {}')
session=$(echo "$input" | jq -r '.session_id // "unknown"')

sqlite3 "$HOOKS_DB" >/dev/null <<SQL || true
PRAGMA busy_timeout=1000;
INSERT INTO audit_events (ts, session, tool, input)
VALUES ('$(_sql_escape "$ts")', '$(_sql_escape "$session")', '$(_sql_escape "$tool")', '$(_sql_escape "$tool_input")');
SQL

_maybe_prune_hooks_db

# Pass through stdin to next hook in chain
echo "$input"
exit 0
