#!/usr/bin/env bash
# hooks-report.sh — ANSI-colored analytics & monitoring report for hooks.db
# Usage:  hooks-report.sh            — visual report (sections a–g)
#         hooks-report.sh --export   — OTel-aligned JSON for piping to Claude/collector
# Sources db-init.sh for $HOOKS_DB and shared helpers.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=db-init.sh
source "$SCRIPT_DIR/db-init.sh"

command -v sqlite3 >/dev/null 2>&1 || { printf 'Error: sqlite3 not found\n' >&2; exit 1; }
[ -f "$HOOKS_DB" ]              || { printf 'Error: hooks.db not found at %s\n' "$HOOKS_DB" >&2; exit 1; }

# ANSI colors
BOLD=$'\033[1m'
CYAN=$'\033[36m'
RED=$'\033[31m'
GREEN=$'\033[32m'
YELLOW=$'\033[33m'
RESET=$'\033[0m'
BCYAN=$'\033[1;36m'

_hdr() {
  printf "\n${BCYAN}══════════════════════════════════════════════════════════════${RESET}\n"
  printf "${BCYAN}  %s${RESET}\n" "$1"
  printf "${BCYAN}══════════════════════════════════════════════════════════════${RESET}\n"
}
_sep() { printf "${CYAN}──────────────────────────────────────────────────────────────${RESET}\n"; }

# sqlite3 query with stdout output (unlike _db_exec which redirects to /dev/null)
# No PRAGMA busy_timeout — it emits a result row that would corrupt pipe reads.
# Report is read-only so lock contention is not a concern.
_q() {
  sqlite3 -separator '|' "$HOOKS_DB" <<SQL
$1
SQL
}

# Configured timeout per step name (ms); 0 = unknown/not configured
_timeout_for() {
  case "$1" in
    audit-logger)        echo 2000  ;;
    mermaid-lint)        echo 35000 ;;
    no-verify-gate)      echo 5000  ;;
    check-pr-labels)     echo 65000 ;;
    phi-check)           echo 15000 ;;
    lint-check)          echo 30000 ;;
    migration-check)     echo 5000  ;;
    stop-checks)         echo 30000 ;;
    *)                   echo 0     ;;
  esac
}

# ─── a) Health Check ──────────────────────────────────────────────────────────
section_health() {
  _hdr "a) Health Check (last 24h)"
  printf "\n"

  _q "
SELECT
  COUNT(*)                                                                                    AS total_runs,
  SUM(CASE WHEN exit_code != 0 AND step NOT IN ('codex-review') THEN 1 ELSE 0 END)          AS failures,
  ROUND(100.0 * SUM(CASE WHEN exit_code != 0 AND step NOT IN ('codex-review') THEN 1 ELSE 0 END)
        / NULLIF(COUNT(*), 0), 1)                                                            AS fail_pct,
  SUM(CASE WHEN exit_code != 0 AND step IN ('codex-review') THEN 1 ELSE 0 END)              AS review_findings,
  SUM(CASE WHEN step IN ('codex-review') THEN 1 ELSE 0 END)                                  AS review_runs,
  SUM(duration_ms)                                                                            AS total_overhead_ms,
  MAX(duration_ms)                                                                            AS max_latency_ms,
  SUM(CASE WHEN duration_ms > 5000 THEN 1 ELSE 0 END)                                       AS slow_count
FROM hook_metrics
WHERE ts > datetime('now', '-1 day');" \
  | while IFS='|' read -r total failures fail_pct review_findings review_runs overhead max_lat slow; do
      printf "  Total runs:       %s\n" "${total:-0}"

      if [ "${failures:-0}" -gt 0 ] 2>/dev/null; then
        printf "  ${RED}Failures:         %s (${fail_pct:-0}%%)${RESET}\n" "$failures"
      else
        printf "  ${GREEN}Failures:         0${RESET}\n"
      fi

      if [ "${review_runs:-0}" -gt 0 ] 2>/dev/null; then
        printf "  Review findings:  %s / %s runs (exit=1 = blocked)\n" \
          "${review_findings:-0}" "${review_runs:-0}"
      fi

      printf "  Total overhead:   %s ms\n" "${overhead:-0}"

      if [ "${max_lat:-0}" -gt 5000 ] 2>/dev/null; then
        printf "  ${RED}Max latency:      %s ms${RESET}\n" "$max_lat"
      else
        printf "  Max latency:      %s ms\n" "${max_lat:-0}"
      fi

      if [ "${slow:-0}" -gt 0 ] 2>/dev/null; then
        printf "  ${YELLOW}Runs >5s:         %s${RESET}\n" "$slow"
      else
        printf "  ${GREEN}Runs >5s:         0${RESET}\n"
      fi
    done
}

# ─── b) Failure Report ────────────────────────────────────────────────────────
section_failures() {
  _hdr "b) Failure Report"

  printf "\n${BOLD}Per-step failure rates:${RESET}\n"
  _q "
SELECT step, COUNT(*) AS failures
FROM hook_metrics
WHERE exit_code != 0
  AND step NOT IN ('codex-review')
GROUP BY step
ORDER BY failures DESC;" \
  | while IFS='|' read -r step count; do
      printf "  ${RED}%-30s %s failures${RESET}\n" "$step" "$count"
    done

  printf "\n"; _sep
  printf "${BOLD}Exit code breakdown per step:${RESET}\n"
  _q "
SELECT step, exit_code, COUNT(*) AS count
FROM hook_metrics
WHERE exit_code != 0
  AND step NOT IN ('codex-review')
GROUP BY step, exit_code
ORDER BY count DESC;" \
  | while IFS='|' read -r step code count; do
      printf "  %-30s exit=%-5s %s\n" "$step" "$code" "$count"
    done

  printf "\n"; _sep
  printf "${BOLD}Review hooks (exit=1 = findings blocked commit):${RESET}\n"
  _q "
SELECT
  step,
  COUNT(*)                                                   AS total_runs,
  SUM(CASE WHEN exit_code != 0 THEN 1 ELSE 0 END)           AS findings,
  ROUND(100.0 * SUM(CASE WHEN exit_code != 0 THEN 1 ELSE 0 END)
        / NULLIF(COUNT(*), 0), 1)                           AS findings_pct
FROM hook_metrics
WHERE step IN ('codex-review')
GROUP BY step;" \
  | while IFS='|' read -r step runs findings pct; do
      printf "  %-30s %s runs  %s with findings (%s%%)\n" "$step" "$runs" "$findings" "$pct"
    done

  printf "\n"; _sep
  printf "${BOLD}Exit-127 root causes (command not found):${RESET}\n"
  _q "
SELECT cmd, COUNT(*) AS count
FROM hook_metrics
WHERE exit_code = 127
GROUP BY cmd
ORDER BY count DESC
LIMIT 10;" \
  | while IFS='|' read -r cmd count; do
      printf "  ${RED}%-55s %s occurrences${RESET}\n" "$cmd" "$count"
    done

  printf "\n"; _sep
  printf "${BOLD}Timeout proximity — audit-logger (>1500ms = >75%% of 2s timeout):${RESET}\n"
  _q "
SELECT step, duration_ms, ts
FROM hook_metrics
WHERE duration_ms > 1500
ORDER BY duration_ms DESC
LIMIT 10;" \
  | while IFS='|' read -r step dur ts; do
      printf "  ${YELLOW}%-30s %6s ms  (%s)${RESET}\n" "$step" "$dur" "$ts"
    done
}

# ─── c) Performance Report ────────────────────────────────────────────────────
section_performance() {
  _hdr "c) Performance Report"

  printf "\n${BOLD}Per-step avg / p95 / max duration (ms):${RESET}\n"
  _q "
WITH ranked AS (
  SELECT
    step,
    duration_ms,
    ROW_NUMBER() OVER (PARTITION BY step ORDER BY duration_ms) AS rn,
    COUNT(*)     OVER (PARTITION BY step)                      AS cnt
  FROM hook_metrics
  WHERE duration_ms > 0
)
SELECT
  step,
  ROUND(AVG(duration_ms), 1)                                                 AS avg_ms,
  MAX(CASE WHEN rn = CAST(CEIL(0.95 * cnt) AS INTEGER) THEN duration_ms END) AS p95_ms,
  MAX(duration_ms)                                                             AS max_ms,
  MAX(cnt)                                                                     AS total_n
FROM ranked
GROUP BY step
ORDER BY avg_ms DESC;" \
  | while IFS='|' read -r step avg p95 maxd total; do
      printf "  %-30s  avg=%7s  p95=%7s  max=%7s  n=%s\n" \
        "$step" "${avg:-?}" "${p95:-?}" "${maxd:-?}" "${total:-0}"
    done

  printf "\n"; _sep
  printf "${BOLD}Timeout proximity (max duration as %% of configured timeout):${RESET}\n"
  _q "
SELECT step, MAX(duration_ms) AS max_ms
FROM hook_metrics
WHERE duration_ms > 0
GROUP BY step;" \
  | while IFS='|' read -r step maxd; do
      limit=$(_timeout_for "$step")
      if [ "$limit" -gt 0 ] 2>/dev/null; then
        pct=$(awk -v m="$maxd" -v t="$limit" 'BEGIN{printf "%.0f", m/t*100}')
        if   [ "$pct" -ge 80 ]; then
          printf "  ${RED}%-30s max=%6s ms  limit=%6s ms  %3s%% used${RESET}\n"    "$step" "$maxd" "$limit" "$pct"
        elif [ "$pct" -ge 50 ]; then
          printf "  ${YELLOW}%-30s max=%6s ms  limit=%6s ms  %3s%% used${RESET}\n" "$step" "$maxd" "$limit" "$pct"
        else
          printf "  ${GREEN}%-30s max=%6s ms  limit=%6s ms  %3s%% used${RESET}\n"  "$step" "$maxd" "$limit" "$pct"
        fi
      else
        printf "  %-30s max=%6s ms  (no timeout configured)\n" "$step" "$maxd"
      fi
    done
}

# ─── d) Usage Patterns ────────────────────────────────────────────────────────
section_usage() {
  _hdr "d) Usage Patterns"

  printf "\n${BOLD}Tool distribution (audit_events, all time):${RESET}\n"
  _q "
SELECT tool, COUNT(*) AS count
FROM audit_events
GROUP BY tool
ORDER BY count DESC;" \
  | while IFS='|' read -r tool count; do
      printf "  %-25s %s\n" "$tool" "$count"
    done

  printf "\n"; _sep
  printf "${BOLD}Sessions (last 7 days):${RESET}\n"
  _q "
SELECT
  COUNT(DISTINCT session)                                                    AS sessions,
  COUNT(*)                                                                   AS total_events,
  ROUND(1.0 * COUNT(*) / NULLIF(COUNT(DISTINCT session), 0), 1)            AS avg_per_session
FROM audit_events
WHERE ts > datetime('now','-7 days');" \
  | while IFS='|' read -r sessions events avg; do
      printf "  Sessions:           %s\n" "$sessions"
      printf "  Total events:       %s\n" "$events"
      printf "  Avg/session:        %s\n" "$avg"
    done

  printf "\n"; _sep
  printf "${BOLD}Most-edited files (top 10, Edit+Write):${RESET}\n"
  _q "
SELECT json_extract(input, '$.file_path') AS file_path, COUNT(*) AS count
FROM audit_events
WHERE tool IN ('Edit','Write')
  AND json_extract(input, '$.file_path') IS NOT NULL
GROUP BY file_path
ORDER BY count DESC
LIMIT 10;" \
  | while IFS='|' read -r file count; do
      printf "  %-65s %s\n" "$file" "$count"
    done

  printf "\n"; _sep
  printf "${BOLD}Bash command categories — first word (top 15):${RESET}\n"
  _q "
SELECT
  TRIM(SUBSTR(
    json_extract(input, '$.command'),
    1,
    INSTR(json_extract(input, '$.command') || ' ', ' ') - 1
  )) AS category,
  COUNT(*) AS count
FROM audit_events
WHERE tool = 'Bash'
  AND json_extract(input, '$.command') IS NOT NULL
  AND json_extract(input, '$.command') != ''
GROUP BY category
ORDER BY count DESC
LIMIT 15;" \
  | while IFS='|' read -r cat count; do
      printf "  %-35s %s\n" "$cat" "$count"
    done
}

# ─── e) Data Quality ──────────────────────────────────────────────────────────
section_quality() {
  _hdr "e) Data Quality"
  printf "\n"

  zero=$(_q "SELECT COUNT(*) FROM hook_metrics WHERE duration_ms = 0 AND real_s = 0;")
  printf "  Zero-timing rows:  "
  if [ "${zero:-0}" -gt 0 ] 2>/dev/null; then
    printf "${YELLOW}%s${RESET}\n" "$zero"
  else
    printf "${GREEN}%s${RESET}\n" "${zero:-0}"
  fi

  unknown=$(_q "SELECT COUNT(*) FROM hook_metrics WHERE hook = '' OR hook IS NULL;")
  printf "  Unknown hook rows: "
  if [ "${unknown:-0}" -gt 0 ] 2>/dev/null; then
    printf "${YELLOW}%s${RESET}\n" "$unknown"
  else
    printf "${GREEN}%s${RESET}\n" "${unknown:-0}"
  fi

  printf "\n"; _sep
  printf "${BOLD}Duplicate detection (same step+exit_code+ts truncated to second):${RESET}\n"
  _q "
SELECT step, exit_code, strftime('%Y-%m-%dT%H:%M:%S', ts) AS ts_sec, COUNT(*) AS n
FROM hook_metrics
GROUP BY step, exit_code, ts_sec
HAVING n > 1
ORDER BY n DESC
LIMIT 10;" \
  | while IFS='|' read -r step code ts_sec count; do
      printf "  ${YELLOW}%-30s exit=%-5s n=%-3s %s${RESET}\n" "$step" "$code" "$count" "$ts_sec"
    done
}

# ─── f) Per-Project Cost ──────────────────────────────────────────────────────
section_projects() {
  _hdr "f) Per-Project Cost (last 7d)"

  printf "\n${BOLD}Overhead by repo (total ms / failures / runs):${RESET}\n"
  _q "
SELECT
  COALESCE(NULLIF(REPLACE(repo, '/Users/$(whoami)/Code/', ''), ''), '(global/unknown)') AS project,
  SUM(duration_ms)                                                                        AS total_ms,
  ROUND(SUM(duration_ms) / 1000.0 / 60.0, 1)                                            AS total_min,
  COUNT(*)                                                                                AS runs,
  SUM(CASE WHEN exit_code != 0 THEN 1 ELSE 0 END)                                       AS failures
FROM hook_metrics
WHERE ts > datetime('now', '-7 days')
GROUP BY repo
ORDER BY total_ms DESC
LIMIT 15;" \
  | while IFS='|' read -r project total_ms total_min runs failures; do
      if [ "${failures:-0}" -gt 0 ] 2>/dev/null; then
        printf "  %-35s %8s ms  %5s min  %5s runs  ${RED}%s failures${RESET}\n" \
          "$project" "$total_ms" "$total_min" "$runs" "$failures"
      else
        printf "  %-35s %8s ms  %5s min  %5s runs\n" \
          "$project" "$total_ms" "$total_min" "$runs"
      fi
    done

  printf "\n"; _sep
  printf "${BOLD}Top steps per repo (last 7d):${RESET}\n"
  _q "
SELECT
  COALESCE(NULLIF(REPLACE(repo, '/Users/$(whoami)/Code/', ''), ''), '(global/unknown)') AS project,
  step,
  COUNT(*)             AS runs,
  SUM(duration_ms)     AS total_ms
FROM hook_metrics
WHERE ts > datetime('now', '-7 days')
GROUP BY repo, step
ORDER BY repo, total_ms DESC;" \
  | awk -F'|' '
    {
      if ($1 != prev) { printf "\n  %s:\n", $1; prev = $1; n = 0 }
      if (n++ < 3) printf "    %-25s %6s runs  %s ms\n", $2, $3, $4
    }'
}

# ─── Trend helpers ────────────────────────────────────────────────────────────

# _bar val max_val [width]  — proportional Unicode bar; default width=30
_bar() {
  local val=$1 max=$2 width=${3:-30}
  local filled empty
  filled=$(awk -v v="$val" -v m="$max" -v w="$width" 'BEGIN{
    n = (m > 0) ? int(v/m * w + 0.5) : 0
    if (n > w) n = w
    for (i=0; i<n; i++) printf "█"
  }')
  empty=$(awk -v v="$val" -v m="$max" -v w="$width" 'BEGIN{
    n = (m > 0) ? int(v/m * w + 0.5) : 0
    if (n > w) n = w
    rest = w - n
    for (i=0; i<rest; i++) printf "░"
  }')
  printf '%s%s' "$filled" "$empty"
}

# _trend_badge type  — colored severity prefix tag
_trend_badge() {
  case "$1" in
    REGR)  printf "${RED}[REGR]${RESET}"  ;;
    FIXED) printf "${GREEN}[FIXED]${RESET}" ;;
    NEW)   printf "${CYAN}[NEW]${RESET}"  ;;
    GONE)  printf "${YELLOW}[GONE]${RESET}" ;;
    SLOW)  printf "${RED}[SLOW]${RESET}"  ;;
  esac
}

# _pct_change cur prev polarity  — returns colored "+X.X%" or "-X.X%"
# polarity: lower_better | higher_better | neutral
_pct_change() {
  local cur=$1 prev=$2 polarity=${3:-neutral}
  awk -v c="$cur" -v p="$prev" -v pol="$polarity" \
      -v red="$RED" -v green="$GREEN" -v reset="$RESET" 'BEGIN{
    if (p == 0) { printf "(new)"; exit }
    pct = (c - p) / p * 100
    sign = (pct >= 0) ? "+" : ""
    label = sprintf("%s%.1f%%", sign, pct)
    color = ""
    if (pol == "lower_better") {
      if      (pct >  10) color = red
      else if (pct < -10) color = green
    } else if (pol == "higher_better") {
      if      (pct < -10) color = red
      else if (pct >  10) color = green
    }
    printf "%s%s%s", color, label, (color != "" ? reset : "")
  }'
}

# ─── g) Week-over-Week Trends ─────────────────────────────────────────────────
# Skip list for coverage-gap noise (known test/debug hooks)
_SKIP_HOOKS='fake-fail|ok-step|echo|test-hook|main'

section_trends() {
  _hdr "g) Week-over-Week Trends (last 7d vs prior 7d)"

  # ── 4. Summary (shown first for quick orientation) ──────────────────────────
  printf "\n${BOLD}Summary:${RESET}\n"
  _q "
SELECT
  SUM(CASE WHEN ts > datetime('now','-7 days')  THEN 1 ELSE 0 END)                                                 AS cur_runs,
  SUM(CASE WHEN ts BETWEEN datetime('now','-14 days') AND datetime('now','-7 days') THEN 1 ELSE 0 END)             AS prev_runs,
  SUM(CASE WHEN ts > datetime('now','-7 days')  AND exit_code != 0 AND step NOT IN ('codex-review') THEN 1 ELSE 0 END) AS cur_fail,
  SUM(CASE WHEN ts BETWEEN datetime('now','-14 days') AND datetime('now','-7 days') AND exit_code != 0 AND step NOT IN ('codex-review') THEN 1 ELSE 0 END) AS prev_fail,
  ROUND(100.0 * SUM(CASE WHEN ts > datetime('now','-7 days')  AND exit_code != 0 AND step NOT IN ('codex-review') THEN 1 ELSE 0 END) / NULLIF(SUM(CASE WHEN ts > datetime('now','-7 days') THEN 1 ELSE 0 END),0),1) AS cur_rate,
  ROUND(100.0 * SUM(CASE WHEN ts BETWEEN datetime('now','-14 days') AND datetime('now','-7 days') AND exit_code != 0 AND step NOT IN ('codex-review') THEN 1 ELSE 0 END) / NULLIF(SUM(CASE WHEN ts BETWEEN datetime('now','-14 days') AND datetime('now','-7 days') THEN 1 ELSE 0 END),0),1) AS prev_rate,
  SUM(CASE WHEN ts > datetime('now','-7 days')  THEN duration_ms ELSE 0 END)                                       AS cur_ms,
  SUM(CASE WHEN ts BETWEEN datetime('now','-14 days') AND datetime('now','-7 days') THEN duration_ms ELSE 0 END)   AS prev_ms
FROM hook_metrics
WHERE ts > datetime('now','-14 days');" \
  | while IFS='|' read -r cr pr cf pf crate prate cms pms; do
      printf "  %-14s  %7s   %7s   " "Metric" "Last 7d" "Prior 7d"
      printf "  %s\n" "Delta"
      printf "  %-14s  %7s   %7s   " "──────────────" "───────" "───────"
      printf "  %s\n" "───────────────"

      # Runs
      rdelta=$(( ${cr:-0} - ${pr:-0} ))
      rsign=$([ "$rdelta" -ge 0 ] && echo "+" || echo "")
      printf "  %-14s  %7s   %7s   " "Runs" "${cr:-0}" "${pr:-0}"
      printf "  %s%s  (%s)\n" "$rsign" "$rdelta" "$(_pct_change "${cr:-0}" "${pr:-0}" neutral)"

      # Failures
      fdelta=$(( ${cf:-0} - ${pf:-0} ))
      fsign=$([ "$fdelta" -ge 0 ] && echo "+" || echo "")
      if [ "${cf:-0}" -gt "${pf:-0}" ] 2>/dev/null; then
        printf "  %-14s  ${RED}%7s${RESET}   %7s   " "Failures" "${cf:-0}" "${pf:-0}"
        printf "  ${RED}%s%s  (%s)${RESET}\n" "$fsign" "$fdelta" "$(_pct_change "${cf:-0}" "${pf:-0}" lower_better)"
      else
        printf "  %-14s  ${GREEN}%7s${RESET}   %7s   " "Failures" "${cf:-0}" "${pf:-0}"
        printf "  ${GREEN}%s%s  (%s)${RESET}\n" "$fsign" "$fdelta" "$(_pct_change "${cf:-0}" "${pf:-0}" lower_better)"
      fi

      # Failure rate
      rdiff=$(awk -v a="${crate:-0}" -v b="${prate:-0}" 'BEGIN{d=a-b; printf "%+.1fpp", d}')
      printf "  %-14s  %6s%%   %6s%%   " "Fail rate" "${crate:-0}" "${prate:-0}"
      if awk -v a="${crate:-0}" -v b="${prate:-0}" 'BEGIN{exit (a > b) ? 0 : 1}' 2>/dev/null; then
        printf "  ${RED}%s${RESET}\n" "$rdiff"
      else
        printf "  ${GREEN}%s${RESET}\n" "$rdiff"
      fi

      # Overhead
      cur_min=$(awk -v ms="${cms:-0}" 'BEGIN{printf "%.1f", ms/60000}')
      prev_min=$(awk -v ms="${pms:-0}" 'BEGIN{printf "%.1f", ms/60000}')
      mdelta=$(awk -v a="${cms:-0}" -v b="${pms:-0}" 'BEGIN{
        d = (a-b)/60000; printf "%+.1f min", d}')
      printf "  %-14s  %6s m   %6s m   " "Overhead" "$cur_min" "$prev_min"
      printf "  %s  (%s)\n" "$mdelta" "$(_pct_change "${cms:-0}" "${pms:-0}" neutral)"
    done

  # ── 1. Failure Trends ───────────────────────────────────────────────────────
  printf "\n"; _sep
  printf "${BOLD}Failure Trends:${RESET}\n"

  # Find max failures across both periods for bar scaling
  max_fail=$(_q "
SELECT MAX(mx) FROM (
  SELECT SUM(CASE WHEN ts > datetime('now','-7 days') AND exit_code != 0 THEN 1 ELSE 0 END) AS mx
  FROM hook_metrics WHERE ts > datetime('now','-14 days') AND step NOT IN ('codex-review')
  GROUP BY step
  UNION ALL
  SELECT SUM(CASE WHEN ts BETWEEN datetime('now','-14 days') AND datetime('now','-7 days') AND exit_code != 0 THEN 1 ELSE 0 END)
  FROM hook_metrics WHERE ts > datetime('now','-14 days') AND step NOT IN ('codex-review')
  GROUP BY step
);")
  max_fail="${max_fail%%.*}"
  [ "${max_fail:-0}" -lt 1 ] && max_fail=1

  # Regressions (failures increased >10%)
  printf "\n"
  _q "
SELECT
  step,
  SUM(CASE WHEN ts > datetime('now','-7 days') AND exit_code != 0 THEN 1 ELSE 0 END)                                    AS cur_f,
  SUM(CASE WHEN ts BETWEEN datetime('now','-14 days') AND datetime('now','-7 days') AND exit_code != 0 THEN 1 ELSE 0 END) AS prev_f,
  SUM(CASE WHEN ts > datetime('now','-7 days') THEN 1 ELSE 0 END)                                                        AS cur_r,
  SUM(CASE WHEN ts BETWEEN datetime('now','-14 days') AND datetime('now','-7 days') THEN 1 ELSE 0 END)                    AS prev_r
FROM hook_metrics
WHERE ts > datetime('now','-14 days')
  AND step NOT IN ('codex-review')
GROUP BY step
HAVING (cur_f > prev_f AND (prev_f = 0 OR CAST(cur_f - prev_f AS REAL)/prev_f > 0.1))
   AND (cur_r + prev_r) >= 5
ORDER BY (cur_f - prev_f) DESC;" \
  | while IFS='|' read -r step cf pf cr pr; do
      delta=$(( cf - pf ))
      pct=$(_pct_change "$cf" "$pf" lower_better)
      printf "  %s  %s\n" "$(_trend_badge REGR)" "$step"
      printf "    Prior  %s  %4s failures\n" "$(_bar "$pf" "$max_fail")" "$pf"
      printf "    ${RED}Last   %s  %4s failures${RESET}   ▲ +%s (%s)\n" \
        "$(_bar "$cf" "$max_fail")" "$cf" "$delta" "$pct"
      printf "\n"
    done

  # Improvements (failures decreased >10%)
  _q "
SELECT
  step,
  SUM(CASE WHEN ts > datetime('now','-7 days') AND exit_code != 0 THEN 1 ELSE 0 END)                                    AS cur_f,
  SUM(CASE WHEN ts BETWEEN datetime('now','-14 days') AND datetime('now','-7 days') AND exit_code != 0 THEN 1 ELSE 0 END) AS prev_f
FROM hook_metrics
WHERE ts > datetime('now','-14 days')
  AND step NOT IN ('codex-review')
GROUP BY step
HAVING prev_f > 0 AND cur_f < prev_f AND (prev_f = 0 OR CAST(prev_f - cur_f AS REAL)/prev_f > 0.1)
   AND (
     SUM(CASE WHEN ts > datetime('now','-7 days') THEN 1 ELSE 0 END) +
     SUM(CASE WHEN ts BETWEEN datetime('now','-14 days') AND datetime('now','-7 days') THEN 1 ELSE 0 END)
   ) >= 5
ORDER BY (prev_f - cur_f) DESC;" \
  | while IFS='|' read -r step cf pf; do
      delta=$(( pf - cf ))
      pct=$(_pct_change "$cf" "$pf" lower_better)
      printf "  %s  %s\n" "$(_trend_badge FIXED)" "$step"
      printf "    Prior  %s  %4s failures\n" "$(_bar "$pf" "$max_fail")" "$pf"
      printf "    ${GREEN}Last   %s  %4s failures${RESET}   ▼ -%s (%s)\n" \
        "$(_bar "$cf" "$max_fail")" "$cf" "$delta" "$pct"
      printf "\n"
    done

  # ── 2. Coverage Gaps ────────────────────────────────────────────────────────
  printf "\n"; _sep
  printf "${BOLD}Coverage Gaps:${RESET}\n\n"

  # Steps that stopped running
  _q "
SELECT step,
  SUM(CASE WHEN ts > datetime('now','-7 days') THEN 1 ELSE 0 END)                                                     AS cur_r,
  SUM(CASE WHEN ts BETWEEN datetime('now','-14 days') AND datetime('now','-7 days') THEN 1 ELSE 0 END)                AS prev_r
FROM hook_metrics
WHERE ts > datetime('now','-14 days')
GROUP BY step
HAVING cur_r = 0 AND prev_r >= 5;" \
  | while IFS='|' read -r step cr pr; do
      # Skip known noise hooks
      if printf '%s' "$step" | grep -qE "^(${_SKIP_HOOKS})$"; then continue; fi
      printf "  %s  %-30s was %s runs    ${YELLOW}⚠ stopped running${RESET}\n" \
        "$(_trend_badge GONE)" "$step" "$pr"
    done

  # Steps that are new this period
  _q "
SELECT step,
  SUM(CASE WHEN ts > datetime('now','-7 days') THEN 1 ELSE 0 END)                                                     AS cur_r,
  SUM(CASE WHEN ts BETWEEN datetime('now','-14 days') AND datetime('now','-7 days') THEN 1 ELSE 0 END)                AS prev_r
FROM hook_metrics
WHERE ts > datetime('now','-14 days')
GROUP BY step
HAVING prev_r = 0 AND cur_r >= 5;" \
  | while IFS='|' read -r step cr pr; do
      if printf '%s' "$step" | grep -qE "^(${_SKIP_HOOKS})$"; then continue; fi
      printf "  %s  %-30s now %s runs    ${CYAN}★ new step${RESET}\n" \
        "$(_trend_badge NEW)" "$step" "$cr"
    done

  # ── 3. Latency Regressions ──────────────────────────────────────────────────
  printf "\n"; _sep
  printf "${BOLD}Latency Regressions (avg duration increased >15%%):${RESET}\n\n"

  max_lat=$(_q "
SELECT MAX(mx) FROM (
  SELECT ROUND(AVG(CASE WHEN ts > datetime('now','-7 days') THEN duration_ms END),0) AS mx
  FROM hook_metrics WHERE ts > datetime('now','-14 days') AND duration_ms > 0
  GROUP BY step
  UNION ALL
  SELECT ROUND(AVG(CASE WHEN ts BETWEEN datetime('now','-14 days') AND datetime('now','-7 days') THEN duration_ms END),0)
  FROM hook_metrics WHERE ts > datetime('now','-14 days') AND duration_ms > 0
  GROUP BY step
);")
  max_lat="${max_lat%%.*}"
  [ "${max_lat:-0}" -lt 1 ] && max_lat=1

  _q "
SELECT
  step,
  ROUND(AVG(CASE WHEN ts > datetime('now','-7 days') THEN duration_ms END), 0)                                              AS cur_avg,
  ROUND(AVG(CASE WHEN ts BETWEEN datetime('now','-14 days') AND datetime('now','-7 days') THEN duration_ms END), 0)         AS prev_avg,
  SUM(CASE WHEN ts > datetime('now','-7 days') THEN 1 ELSE 0 END) + SUM(CASE WHEN ts BETWEEN datetime('now','-14 days') AND datetime('now','-7 days') THEN 1 ELSE 0 END) AS total_n
FROM hook_metrics
WHERE ts > datetime('now','-14 days')
  AND duration_ms > 0
GROUP BY step
HAVING cur_avg IS NOT NULL AND prev_avg IS NOT NULL
  AND cur_avg > prev_avg
  AND CAST(cur_avg - prev_avg AS REAL) / NULLIF(prev_avg,0) > 0.15
  AND total_n >= 5
ORDER BY (cur_avg - prev_avg) DESC;" \
  | while IFS='|' read -r step ca pa tn; do
      delta_ms=$(awk -v c="$ca" -v p="$pa" 'BEGIN{printf "%.0f", c-p}')
      pct=$(_pct_change "$ca" "$pa" lower_better)
      # Choose badge based on severity
      pct_raw=$(awk -v c="$ca" -v p="$pa" 'BEGIN{printf "%.0f", (c-p)/p*100}')
      if [ "${pct_raw:-0}" -ge 30 ] 2>/dev/null; then
        badge=$(_trend_badge SLOW)
        color=$RED
      else
        badge="${YELLOW}[SLOW]${RESET}"
        color=$YELLOW
      fi
      printf "  %s  %s\n" "$badge" "$step"
      printf "    Prior  %s  %7s ms avg\n" "$(_bar "$pa" "$max_lat")" "$pa"
      printf "    ${color}Last   %s  %7s ms avg${RESET}   ▲ +%sms (%s)\n" \
        "$(_bar "$ca" "$max_lat")" "$ca" "$delta_ms" "$pct"
      printf "\n"
    done
}

# ─── export_json ──────────────────────────────────────────────────────────────
# Usage:
#   bash hooks-report.sh            # visual ANSI report
#   bash hooks-report.sh --export   # OTel-aligned JSON (pipe to Claude or a collector)
#
# Export + Claude analysis:
#   bash hooks-report.sh --export | claude -p \
#     "Analyze this hooks telemetry. Identify: 1) errors to fix, \
#      2) performance to optimize, 3) coverage gaps to close. Suggest next steps."
export_json() {
  local ts_now
  ts_now=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

  # Summary query
  local summary
  summary=$(_q "
SELECT
  SUM(CASE WHEN ts > datetime('now','-7 days') THEN 1 ELSE 0 END)                AS cur_runs,
  SUM(CASE WHEN ts BETWEEN datetime('now','-14 days') AND datetime('now','-7 days') THEN 1 ELSE 0 END) AS prev_runs,
  SUM(CASE WHEN ts > datetime('now','-7 days') AND exit_code != 0 AND step NOT IN ('codex-review') THEN 1 ELSE 0 END) AS cur_fail,
  SUM(CASE WHEN ts BETWEEN datetime('now','-14 days') AND datetime('now','-7 days') AND exit_code != 0 AND step NOT IN ('codex-review') THEN 1 ELSE 0 END) AS prev_fail,
  ROUND(100.0 * SUM(CASE WHEN ts > datetime('now','-7 days') AND exit_code != 0 AND step NOT IN ('codex-review') THEN 1 ELSE 0 END) / NULLIF(SUM(CASE WHEN ts > datetime('now','-7 days') THEN 1 ELSE 0 END),0),1) AS cur_rate,
  ROUND(100.0 * SUM(CASE WHEN ts BETWEEN datetime('now','-14 days') AND datetime('now','-7 days') AND exit_code != 0 AND step NOT IN ('codex-review') THEN 1 ELSE 0 END) / NULLIF(SUM(CASE WHEN ts BETWEEN datetime('now','-14 days') AND datetime('now','-7 days') THEN 1 ELSE 0 END),0),1) AS prev_rate,
  SUM(CASE WHEN ts > datetime('now','-7 days') THEN duration_ms ELSE 0 END)       AS cur_ms,
  SUM(CASE WHEN ts BETWEEN datetime('now','-14 days') AND datetime('now','-7 days') THEN duration_ms ELSE 0 END) AS prev_ms,
  SUM(CASE WHEN ts > datetime('now','-7 days') AND duration_ms > 5000 THEN 1 ELSE 0 END)  AS cur_slow,
  SUM(CASE WHEN ts BETWEEN datetime('now','-14 days') AND datetime('now','-7 days') AND duration_ms > 5000 THEN 1 ELSE 0 END) AS prev_slow
FROM hook_metrics
WHERE ts > datetime('now','-14 days');")

  printf '{\n'
  printf '  "schema": "claude.hooks.trends/v1",\n'
  printf '  "generated_at": "%s",\n' "$ts_now"
  printf '  "period": {\n'
  printf '    "current":  { "start": "-7d",  "end": "now" },\n'
  printf '    "previous": { "start": "-14d", "end": "-7d" }\n'
  printf '  },\n'

  # Summary block
  printf '  "summary": {\n'
  echo "$summary" | while IFS='|' read -r cr pr cf pf crate prate cms pms cslow pslow; do
    printf '    "current": {\n'
    printf '      "claude.hooks.runs": %s,\n'         "${cr:-0}"
    printf '      "claude.hooks.failures": %s,\n'     "${cf:-0}"
    printf '      "claude.hooks.failure_rate": %s,\n' "${crate:-0}"
    printf '      "claude.hooks.overhead_ms": %s,\n'  "${cms:-0}"
    printf '      "claude.hooks.slow_runs": %s\n'     "${cslow:-0}"
    printf '    },\n'
    printf '    "previous": {\n'
    printf '      "claude.hooks.runs": %s,\n'         "${pr:-0}"
    printf '      "claude.hooks.failures": %s,\n'     "${pf:-0}"
    printf '      "claude.hooks.failure_rate": %s,\n' "${prate:-0}"
    printf '      "claude.hooks.overhead_ms": %s,\n'  "${pms:-0}"
    printf '      "claude.hooks.slow_runs": %s\n'     "${pslow:-0}"
    printf '    }\n'
  done
  printf '  },\n'

  # Failure trends
  printf '  "failure_trends": [\n'
  local first=1
  _q "
SELECT
  step,
  SUM(CASE WHEN ts > datetime('now','-7 days') AND exit_code != 0 THEN 1 ELSE 0 END)                                    AS cur_f,
  SUM(CASE WHEN ts BETWEEN datetime('now','-14 days') AND datetime('now','-7 days') AND exit_code != 0 THEN 1 ELSE 0 END) AS prev_f,
  SUM(CASE WHEN ts > datetime('now','-7 days') THEN 1 ELSE 0 END)                                                        AS cur_r,
  SUM(CASE WHEN ts BETWEEN datetime('now','-14 days') AND datetime('now','-7 days') THEN 1 ELSE 0 END)                    AS prev_r
FROM hook_metrics
WHERE ts > datetime('now','-14 days') AND step NOT IN ('codex-review')
GROUP BY step
HAVING (cur_f != prev_f) AND (cur_r + prev_r) >= 5
ORDER BY ABS(cur_f - prev_f) DESC;" \
  | while IFS='|' read -r step cf pf cr pr; do
      dir=$([ "$cf" -gt "$pf" ] && echo "regression" || echo "improvement")
      delta=$(( cf - pf ))
      pct=$(awk -v c="$cf" -v p="$pf" 'BEGIN{ if(p==0){printf "null"}else{printf "%.1f",(c-p)/p*100} }')
      [ "$first" -eq 1 ] && first=0 || printf ',\n'
      printf '    {\n'
      printf '      "hook.step": "%s",\n' "$step"
      printf '      "current":  { "claude.hooks.failures": %s, "claude.hooks.runs": %s },\n' "$cf" "$cr"
      printf '      "previous": { "claude.hooks.failures": %s, "claude.hooks.runs": %s },\n' "$pf" "$pr"
      printf '      "delta": %s,\n' "$delta"
      printf '      "pct_change": %s,\n' "$pct"
      printf '      "direction": "%s"\n' "$dir"
      printf '    }'
    done
  printf '\n  ],\n'

  # Latency trends
  printf '  "latency_trends": [\n'
  first=1
  _q "
SELECT
  step,
  ROUND(AVG(CASE WHEN ts > datetime('now','-7 days') THEN duration_ms END), 0)                                      AS cur_avg,
  ROUND(AVG(CASE WHEN ts BETWEEN datetime('now','-14 days') AND datetime('now','-7 days') THEN duration_ms END), 0) AS prev_avg
FROM hook_metrics
WHERE ts > datetime('now','-14 days') AND duration_ms > 0
GROUP BY step
HAVING cur_avg IS NOT NULL AND prev_avg IS NOT NULL
  AND ABS(cur_avg - prev_avg) / NULLIF(prev_avg, 0) > 0.15
  AND (
    SUM(CASE WHEN ts > datetime('now','-7 days') THEN 1 ELSE 0 END) +
    SUM(CASE WHEN ts BETWEEN datetime('now','-14 days') AND datetime('now','-7 days') THEN 1 ELSE 0 END)
  ) >= 5
ORDER BY ABS(cur_avg - prev_avg) DESC;" \
  | while IFS='|' read -r step ca pa; do
      dir=$(awk -v c="$ca" -v p="$pa" 'BEGIN{print (c>p)?"regression":"improvement"}')
      delta_ms=$(awk -v c="$ca" -v p="$pa" 'BEGIN{printf "%.0f", c-p}')
      pct=$(awk -v c="$ca" -v p="$pa" 'BEGIN{printf "%.1f",(c-p)/p*100}')
      [ "$first" -eq 1 ] && first=0 || printf ',\n'
      printf '    {\n'
      printf '      "hook.step": "%s",\n' "$step"
      printf '      "current":  { "claude.hooks.duration.avg_ms": %s },\n' "$ca"
      printf '      "previous": { "claude.hooks.duration.avg_ms": %s },\n' "$pa"
      printf '      "delta_ms": %s,\n' "$delta_ms"
      printf '      "pct_change": %s,\n' "$pct"
      printf '      "direction": "%s"\n' "$dir"
      printf '    }'
    done
  printf '\n  ],\n'

  # Coverage gaps
  printf '  "coverage_gaps": [\n'
  first=1
  _q "
SELECT step,
  SUM(CASE WHEN ts > datetime('now','-7 days') THEN 1 ELSE 0 END)                                                 AS cur_r,
  SUM(CASE WHEN ts BETWEEN datetime('now','-14 days') AND datetime('now','-7 days') THEN 1 ELSE 0 END)            AS prev_r
FROM hook_metrics
WHERE ts > datetime('now','-14 days')
GROUP BY step
HAVING (cur_r = 0 AND prev_r >= 5) OR (prev_r = 0 AND cur_r >= 5);" \
  | while IFS='|' read -r step cr pr; do
      if printf '%s' "$step" | grep -qE "^(${_SKIP_HOOKS})$"; then continue; fi
      if [ "${cr:-0}" -eq 0 ] 2>/dev/null; then
        status="stopped"
        cnt="$pr"
        key="previous_runs"
      else
        status="new"
        cnt="$cr"
        key="current_runs"
      fi
      [ "$first" -eq 1 ] && first=0 || printf ',\n'
      printf '    { "hook.step": "%s", "%s": %s, "status": "%s" }' \
        "$step" "$key" "$cnt" "$status"
    done
  printf '\n  ]\n'

  printf '}\n'
}

# ─── Main ─────────────────────────────────────────────────────────────────────
EXPORT_MODE=false
[[ "${1:-}" == "--export" ]] && EXPORT_MODE=true

if $EXPORT_MODE; then
  export_json
else
  section_health
  section_failures
  section_performance
  section_usage
  section_quality
  section_projects
  section_trends

  printf "\n${BCYAN}══════════════════════════════════════════════════════════════${RESET}\n"
  printf "${GREEN}  Report complete — DB: %s${RESET}\n" "$HOOKS_DB"
  printf "${BCYAN}══════════════════════════════════════════════════════════════${RESET}\n\n"
fi
