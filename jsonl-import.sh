#!/usr/bin/env bash
set -euo pipefail

# jsonl-import.sh — Import hooktime JSONL entries into hooks.db (SQLite)
# Usage: ./jsonl-import.sh [--dry-run]

# shellcheck source=db-init.sh
source "$(dirname "$0")/db-init.sh"
_init_hooks_db

DIR="$(cd "$(dirname "$0")" && pwd)"

DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    *) echo "Usage: $0 [--dry-run]" >&2; exit 1 ;;
  esac
done

JSONL_FILE="${CLAUDE_METRICS_LOG:-$HOME/.claude/hook-metrics.log}"

if [ ! -f "$JSONL_FILE" ]; then
  echo "jsonl-import: no JSONL file at $JSONL_FILE — nothing to import" >&2
  exit 0
fi

PYTHON="${DIR}/.venv/bin/python3"
[ -x "$PYTHON" ] || PYTHON=python3

# Read-only query helper
_q() { sqlite3 "$HOOKS_DB" "$1"; }

pre_count=$(_q "SELECT COUNT(*) FROM hook_metrics")

# --- Backup before writing (skip for dry-run) ---
backup=""
if [ "$DRY_RUN" -eq 0 ]; then
  backup="${HOOKS_DB}.pre-import-$(date +%s).bak"
  cp "$HOOKS_DB" "$backup"
fi

# --- Run Python: parse JSONL, dedup, insert via parameterized queries ---
counts=$("$PYTHON" "$DIR/jsonl_import.py" "$JSONL_FILE" "$HOOKS_DB" "$DRY_RUN")
IFS='|' read -r imported skipped errors total <<< "$counts"

# --- Dry run or nothing to import: clean up backup and exit ---
if [ "$DRY_RUN" -eq 1 ] || [ "$imported" -eq 0 ]; then
  [ -n "$backup" ] && rm -f "$backup"
  exit 0
fi

echo "jsonl-import: backup created at $backup" >&2

# --- Validate ---
post_count=$(_q "SELECT COUNT(*) FROM hook_metrics")
expected_count=$((pre_count + imported))

validate_ok=true
validate_err=""

if [ "$post_count" -ne "$expected_count" ]; then
  validate_ok=false
  validate_err="Row count mismatch: expected $expected_count, got $post_count"
fi

if [ "$validate_ok" = true ]; then
  integrity=$(_q "PRAGMA integrity_check")
  if [ "$integrity" != "ok" ]; then
    validate_ok=false
    validate_err="Integrity check failed: $integrity"
  fi
fi

# --- Handle validation result ---
if [ "$validate_ok" = false ]; then
  echo "jsonl-import: VALIDATION FAILED — $validate_err" >&2
  echo "jsonl-import: restoring backup from $backup" >&2
  cp "$backup" "$HOOKS_DB"
  echo "jsonl-import: DB restored to pre-import state" >&2
  exit 1
fi

# Validation passed — clean up backup
rm -f "$backup"
echo "jsonl-import: $total lines, $imported imported, $skipped already exist, $errors parse errors" >&2
echo "jsonl-import: validation passed, backup removed" >&2

_maybe_prune_hooks_db
