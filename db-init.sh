#!/usr/bin/env bash
# Shared SQLite helpers — sourced by audit-logger.sh and hook-metrics.sh

command -v sqlite3 >/dev/null || return 0

HOOKS_DB="${CLAUDE_HOOKS_DB:-$HOME/.claude/hooks.db}"

_init_hooks_db() {
  if [ -f "$HOOKS_DB" ]; then
    # Migrate existing DB: add repo column if missing (SELECT probe is fast no-op after first run)
    if ! sqlite3 "$HOOKS_DB" "SELECT repo FROM hook_metrics LIMIT 0" >/dev/null 2>&1; then
      sqlite3 "$HOOKS_DB" "ALTER TABLE hook_metrics ADD COLUMN repo TEXT DEFAULT ''" >/dev/null 2>&1 || true
    fi
    # Migrate existing DB: add session column if missing
    if ! sqlite3 "$HOOKS_DB" "SELECT session FROM hook_metrics LIMIT 0" >/dev/null 2>&1; then
      sqlite3 "$HOOKS_DB" >/dev/null 2>&1 <<'SQL' || true
ALTER TABLE hook_metrics ADD COLUMN session TEXT DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_hook_metrics_session ON hook_metrics(session) WHERE session != '';
SQL
    fi
    # Migrate existing DB: add stderr_snippet column if missing
    sqlite3 "$HOOKS_DB" "ALTER TABLE hook_metrics ADD COLUMN stderr_snippet TEXT DEFAULT ''" 2>/dev/null || true
    return 0
  fi

  sqlite3 "$HOOKS_DB" >/dev/null <<'SQL'
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS audit_events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT NOT NULL,
    session TEXT NOT NULL,
    tool    TEXT NOT NULL,
    input   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS hook_metrics (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    hook        TEXT NOT NULL,
    step        TEXT NOT NULL,
    cmd         TEXT NOT NULL,
    exit_code   INTEGER NOT NULL,
    duration_ms INTEGER NOT NULL,
    real_s      REAL NOT NULL,
    user_s      REAL NOT NULL,
    sys_s       REAL NOT NULL,
    branch      TEXT DEFAULT '',
    sha         TEXT DEFAULT '',
    host        TEXT DEFAULT '',
    repo        TEXT DEFAULT '',
    session     TEXT DEFAULT '',
    stderr_snippet TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_hook_metrics_session
  ON hook_metrics(session) WHERE session != '';
SQL
}

# SQL single-quote escape (values sanitized with tr -d '\n\r' before interpolation)
_sql_escape() {
  printf '%s' "$1" | sed "s/'/''/g"
}

# sqlite3 wrapper: sets busy_timeout per connection, suppresses stdout
_db_exec() {
  sqlite3 "$HOOKS_DB" >/dev/null <<SQL
PRAGMA busy_timeout=1000;
$1
SQL
}

# Probabilistic pruning (~1% of calls): delete rows older than 30 days
_maybe_prune_hooks_db() {
  if [ $(( RANDOM % 100 )) -eq 0 ]; then
    _db_exec "DELETE FROM audit_events WHERE id IN (SELECT id FROM audit_events WHERE ts < datetime('now','-30 days') LIMIT 500);
              DELETE FROM hook_metrics WHERE id IN (SELECT id FROM hook_metrics WHERE ts < datetime('now','-30 days') LIMIT 500);" >/dev/null 2>&1 || true
  fi
}
