#!/bin/bash
# =============================================================================
# Hook: mermaid-lint
# Event: PostToolUse (Edit, Write)
# Purpose: Lint markdown/mermaid files after edit/write operations
# =============================================================================

set -euo pipefail

# Read JSON input from stdin
input=$(cat)

# Extract file_path from tool input
file_path=$(echo "$input" | jq -r '.tool_input.file_path // empty' 2>/dev/null)

# Exit if no file path
[ -z "$file_path" ] && exit 0

# Check if file matches markdown/mermaid patterns
case "$file_path" in
  *.md|*.markdown|*.mdx|*.mmd|*.mermaid)
    # Run maid linter
    if ! maid "$file_path" 2>&1; then
      escaped=$(printf "Mermaid lint failed for %s" "$file_path" | jq -Rs .)
      echo "{\"hookSpecificOutput\":{\"additionalContext\":$escaped}}"
    fi
    ;;
esac
exit 0
