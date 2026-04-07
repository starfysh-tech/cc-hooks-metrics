#!/usr/bin/env bash
DIR="$(cd "$(dirname "$0")" && pwd)"

# Import hooktime JSONL data before generating the report (idempotent, fast)
if [ -f "$DIR/jsonl-import.sh" ] && [ -f "${CLAUDE_METRICS_LOG:-$HOME/.claude/hook-metrics.log}" ]; then
  "$DIR/jsonl-import.sh" 2>>"${CLAUDE_METRICS_LOG:-$HOME/.claude/hook-metrics.log}.import-errors" || true
fi

# Cached Claude Code version check (24h TTL to avoid Node.js startup latency)
# shellcheck source=version-requirements
if [ -f "$DIR/version-requirements" ]; then
  source "$DIR/version-requirements"
  CC_VERSION_CACHE="$DIR/.cc-version-cache"
  CACHE_TTL_MIN=1440  # 24 hours

  _version_lt() {
    [ "$1" = "$2" ] && return 1
    printf '%s\n%s\n' "$1" "$2" | sort -V | head -n1 | grep -qx "$1"
  }

  if [ ! -f "$CC_VERSION_CACHE" ] || [ "$(find "$CC_VERSION_CACHE" -mmin +$CACHE_TTL_MIN 2>/dev/null)" ]; then
    claude --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 > "$CC_VERSION_CACHE" 2>/dev/null || true
  fi

  _cc_ver=$(cat "$CC_VERSION_CACHE" 2>/dev/null)
  if [ -n "$_cc_ver" ]; then
    if _version_lt "$_cc_ver" "$MIN_CC_VERSION"; then
      printf '\033[31m[!] Claude Code %s is below minimum %s — some hooks may not work\033[0m\n' "$_cc_ver" "$MIN_CC_VERSION" >&2
    elif _version_lt "$_cc_ver" "$RECOMMENDED_CC_VERSION"; then
      printf '\033[33m[i] Claude Code %s — full feature set requires %s+\033[0m\n' "$_cc_ver" "$RECOMMENDED_CC_VERSION" >&2
    fi
  fi
fi

PYTHON="${DIR}/.venv/bin/python3"
[ -x "$PYTHON" ] || PYTHON=python3
PYTHONPATH="$DIR" exec "$PYTHON" -m hooks_report "$@"
