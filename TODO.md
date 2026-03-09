# TODO

## Prioritized

- [ ] **Reduce signal-to-noise in default output** — restructure output tiers: default shows traffic lights + grouped action items + failure/latency trends (~30-40 lines); verbose adds perf table, WoW summary, projects. Group action items by step so a broken step doesn't appear 5 times across sections. See plan: `.claude/plans/sprightly-singing-narwhal.md`

## Parking Lot

Enhancements identified but out of scope for current work. Review before planning next iteration.

- [ ] **Homebrew distribution** — package as a Brew formula with an install script so it can be distributed without manual `rsync`/`install` commands
- [ ] **Sharpen fix suggestions in action items** — "Investigate hook failures" repeated 4x is not actionable; suggestions should be step-specific (e.g., include the actual script path for exit-127, the configured timeout for TIMEOUT items)
- [ ] **Filter performance table to problems only** — current table always shows 12 rows sorted by total_ms; rows with no timeout and normal latency (e.g., `eslint 2.2s (no limit)`) provide no signal and should be hidden unless explicitly requested
- [ ] **Suppress confirmation noise** — green traffic-light categories, FIXED badges, and GONE coverage gaps for test/internal steps are context, not action items; consider hiding or collapsing them
- [ ] **WoW summary table** — the 4-row aggregate (runs/failures/fail rate/overhead) adds context but no specific action; the trends section already highlights specific regressions; consider moving to `--verbose` only
- [ ] **TUI subtitle accuracy** — after returning from the Detail screen, the app subtitle stays as "Detail" until refresh; should restore dashboard subtitle on pop
- [ ] **Configurable thresholds** — `config.py` constants (regression %, timeout %, slow run threshold) are hardcoded; a user-level config file would allow tuning without editing source
- [ ] **audit-logger.sh: single `head` read** — currently reads `$TMPFILE` three times (`tool`, `session`, `full_payload`); consolidate into one `payload_head=$(head -c 65536 "$TMPFILE")` variable (PR #2 review comment)
- [ ] **guard-security.py: extend to MultiEdit** — `MultiEdit` tool (multiple edits in one call) is not yet handled in `FILE_TOOL_PATH_FIELDS`; its payload uses `file_path` but the array of edits may span multiple paths
- [ ] **guard-auto-allow.py: WebFetch safe-list** — `WebFetch` reads from URLs; could be auto-allowed similar to `WebSearch` when added to `READ_ONLY_TOOLS`
- [ ] **broken_hooks: successes CTE misses semantic exit steps** — `successes` CTE counts only `exit_code = 0`; for `SEMANTIC_EXIT_STEPS` (e.g., `codex-review`), exit 1 means "findings found" not failure, so those steps will never show `last_success` and could show misleadingly persistent red severity if they ever hit exit-127
- [ ] **spans.py: add `__post_init__` validation to `Span`** — validate `len(trace_id)==32`, `len(span_id)==16`, `kind in (1,3)`, `status_code in (1,2)`, `start <= end` (PR #2 review comment)
- [ ] **spans.py: consider `IntEnum` for `SpanKind` and `StatusCode`** — replaces magic ints with self-documenting values, serializes to int naturally (PR #2 review comment)
- [ ] **spans.py / db.py: no test coverage** — factory functions, timestamp parsing, redaction logic, ID generation all untested; privacy-sensitive export pipeline warrants attention (PR #2 review comment)
