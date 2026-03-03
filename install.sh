#!/usr/bin/env bash
set -euo pipefail

# -----------------------------------------------------------------------
# cc-hooks-metrics install.sh
# Usage: ./install.sh [--force]
# -----------------------------------------------------------------------

FORCE=0
for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    *) printf '[FAIL] Unknown argument: %s\n' "$arg" >&2; exit 1 ;;
  esac
done

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
HOOKS_DIR="$HOME/.claude/hooks"
VENV="$HOOKS_DIR/.venv"

ok()   { printf '[OK]   %s\n' "$*"; }
warn() { printf '[WARN] %s\n' "$*"; }
fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Phase 1: Preflight
# ---------------------------------------------------------------------------
echo "==> Phase 1: Preflight"

[ -f "$REPO_ROOT/hooks_report/__main__.py" ] \
  || fail "Must run from repo root (hooks_report/__main__.py not found)"
ok "Repo root confirmed"

PY_VER=$(python3 -c "
import sys
if sys.version_info < (3, 10):
    raise SystemExit(f'need 3.10+, got {sys.version_info.major}.{sys.version_info.minor}')
print(f'{sys.version_info.major}.{sys.version_info.minor}')
") || fail "python3 >= 3.10 required — Fix: brew install python@3.12"
ok "python3 $PY_VER"

sqlite3 --version &>/dev/null || fail "sqlite3 not found — required for hooks.db"
ok "sqlite3"

if [ -x /usr/bin/time ]; then
  ok "/usr/bin/time"
else
  warn "/usr/bin/time not found (hook timing will not be captured)"
fi

if jq --version &>/dev/null; then
  ok "jq"
else
  warn "jq not found (optional — \`--export | jq\` piping works without it)"
fi

# Gate on overwrite *before* venv install to avoid wasted work
if [ "$FORCE" -eq 0 ] && [ -d "$HOOKS_DIR" ]; then
  printf '  ~/.claude/hooks/ already exists. Overwrite? [y/N] '
  read -r REPLY || true
  [[ "${REPLY:-n}" =~ ^[Yy]$ ]] \
    || { echo "Deploy skipped. Re-run with --force to skip this prompt."; exit 0; }
fi

# ---------------------------------------------------------------------------
# Phase 2: Python venv + deps
# ---------------------------------------------------------------------------
echo ""
echo "==> Phase 2: Python venv + deps"

if [ -x "$VENV/bin/python3" ] && "$VENV/bin/python3" -c "import textual, rich" 2>/dev/null; then
  ok "Venv already valid (skipping install)"
else
  mkdir -p "$HOOKS_DIR"
  python3 -c "import ensurepip" 2>/dev/null \
    || fail "ensurepip unavailable — Fix: brew install python@3.12"
  python3 -m venv "$VENV" || fail "Failed to create venv at $VENV"
  "$VENV/bin/pip" install --quiet "textual>=8.0,<9.0" "rich>=14.0" \
    || fail "pip install failed — check your network connection"
  "$VENV/bin/python3" -c "import textual, rich" \
    || fail "Import check failed after install — venv may be corrupted"
  ok "Venv created and deps installed ($VENV)"
fi

# ---------------------------------------------------------------------------
# Phase 3: Deploy scripts
# ---------------------------------------------------------------------------
echo ""
echo "==> Phase 3: Deploy scripts"

SCRIPTS=(hook-metrics.sh audit-logger.sh db-init.sh mermaid-lint.sh hooks-report.sh)
for s in "${SCRIPTS[@]}"; do
  [ -f "$REPO_ROOT/$s" ] || fail "Missing source script: $s"
done
[ -d "$REPO_ROOT/hooks_report" ] || fail "Missing source: hooks_report/"
[ -d "$REPO_ROOT/guardrails" ]   || fail "Missing source: guardrails/"

mkdir -p "$HOOKS_DIR"
for s in "${SCRIPTS[@]}"; do
  install -m 755 "$REPO_ROOT/$s" "$HOOKS_DIR/"
done
rsync -a --delete "$REPO_ROOT/hooks_report/" "$HOOKS_DIR/hooks_report/"
rsync -a --delete "$REPO_ROOT/guardrails/"   "$HOOKS_DIR/guardrails/"
ok "Deployed to $HOOKS_DIR"

# ---------------------------------------------------------------------------
# Phase 4: Settings guidance
# ---------------------------------------------------------------------------
echo ""
echo "==> Phase 4: Settings"

SETTINGS="$HOME/.claude/settings.json"
if [ ! -f "$SETTINGS" ]; then
  cp "$REPO_ROOT/settings-example.json" "$SETTINGS"
  ok "Copied settings-example.json → $SETTINGS"
else
  warn "~/.claude/settings.json already exists — merge manually:"
  warn "  diff $REPO_ROOT/settings-example.json $SETTINGS"
fi

# ---------------------------------------------------------------------------
# Phase 5: Validate
# ---------------------------------------------------------------------------
echo ""
echo "==> Phase 5: Validate"

if "$HOOKS_DIR/hooks-report.sh" --static; then
  ok "hooks-report.sh --static passed"
else
  warn "hooks-report.sh returned errors (DB may be empty on first install — this is expected)"
fi

echo ""
echo "Install complete. Run: ~/.claude/hooks/hooks-report.sh"
