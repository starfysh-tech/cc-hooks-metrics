#!/usr/bin/env bash
set -euo pipefail

# shellcheck source=db-init.sh
source "$(dirname "$0")/db-init.sh"
_init_hooks_db

# Optional event type prefix (e.g. PostToolUseFailure). Blank for PostToolUse.
EVENT_TYPE="${1:-}"

# Tee stdin to temp file — full-fidelity passthrough, bounded store
TMPFILE=$(mktemp)
trap 'rm -f "$TMPFILE"' EXIT
cat > "$TMPFILE"

# jq guard — exit 0 so missing jq never blocks Claude Code
command -v jq >/dev/null 2>&1 || { cat "$TMPFILE"; exit 0; }

ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Fallback chain covers PostToolUse (.tool_name), SubagentStart/Stop (.agent_type),
# SessionEnd/UserPromptSubmit (.hook_event_name)
tool=$(head -c 65536 "$TMPFILE" | jq -r '.tool_name // .agent_type // .hook_event_name // "unknown"')
session=$(head -c 65536 "$TMPFILE" | jq -r '.session_id // "unknown"')

# Strip shell-injectable chars before heredoc interpolation
tool=$(printf '%s' "$tool" | tr -d '`$\n\r')
session=$(printf '%s' "$session" | tr -d '`$\n\r')

# Prepend event type for non-PostToolUse events (e.g. "PostToolUseFailure:Write")
[ -n "$EVENT_TYPE" ] && tool="${EVENT_TYPE}:${tool}"

# Full JSON payload — valuable for analysis; bounded to 64KB for shell safety
full_payload=$(head -c 65536 "$TMPFILE")
full_payload=$(printf '%s' "$full_payload" | tr -d '`$')

sqlite3 "$HOOKS_DB" >/dev/null <<SQL || true
PRAGMA busy_timeout=1000;
BEGIN IMMEDIATE;
INSERT INTO audit_events (ts, session, tool, input)
VALUES ('$(_sql_escape "$ts")', '$(_sql_escape "$session")', '$(_sql_escape "$tool")', '$(_sql_escape "$full_payload")');
COMMIT;
SQL

_maybe_prune_hooks_db

# Full-fidelity passthrough — echo original, not potentially truncated variable
cat "$TMPFILE"
exit 0
