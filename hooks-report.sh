#!/usr/bin/env bash
DIR="$(cd "$(dirname "$0")" && pwd)"

# Import hooktime JSONL data before generating the report (idempotent, fast)
if [ -f "$DIR/jsonl-import.sh" ] && [ -f "${CLAUDE_METRICS_LOG:-$HOME/.claude/hook-metrics.log}" ]; then
  "$DIR/jsonl-import.sh" 2>/dev/null || true
fi

PYTHON="${DIR}/.venv/bin/python3"
[ -x "$PYTHON" ] || PYTHON=python3
PYTHONPATH="$DIR" exec "$PYTHON" -m hooks_report "$@"
