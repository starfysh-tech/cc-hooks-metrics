# TODO

## Prioritized

- [ ] **Reduce signal-to-noise in default output** — restructure output tiers: default shows traffic lights + grouped action items + failure/latency trends (~30-40 lines); verbose adds perf table, WoW summary, projects. Group action items by step so a broken step doesn't appear 5 times across sections. See plan: `.claude/plans/sprightly-singing-narwhal.md`

### OTEL Observability Roadmap

Five independently implementable phases. Each has a plan file in `docs/plans/`. Start with Phase 1 — all others depend on it or are independent. See roadmap: `.claude/plans/wise-dancing-raven.md`

- [x] **Phase 1: Session Correlation + OTEL Span Model + Expanded Event Capture** — `session` column in `hook_metrics`, `spans.py` with OTel span model, `--export-spans` CLI flag. Shipped in `feat/phase1-session-correlation-otel-spans`. See plan: `docs/plans/2026-02-28-feat-session-correlation-otel-spans-plan.md`
- [x] **Phase 2: Local Analyses** — add per-step reliability (p50/p90/p99 + pain index), per-repo health profiles, and per-session summaries/timelines to `db.py`. Step/repo queries are Phase 1-independent; session queries require Phase 1 `session` column. See plan: `docs/plans/2026-02-28-feat-local-analyses-step-repo-session-plan.md`
- [x] **Phase 3: TUI Screens + CLI Integration** — add `SessionsScreen` (press `s`) and `StepDrillScreen` (press `t`) to the TUI; add `--sessions` and `--step NAME` CLI flags; fix TUI subtitle bug on screen pop. Requires Phase 2 query layer. See plan: `docs/plans/2026-02-28-feat-tui-screens-sessions-steps-plan.md`
- [x] **Phase 4: Advisor + Feedback Loops** — add `advisor.py` with guardrail tuning suggestions and periodic privacy-safe summaries; add `AdvisorScreen` (press `a`); add `--summary daily|weekly` and `--export-summary` CLI flags. Requires Phase 2 `step_reliability()`; hot sequences require Phase 1. See plan: `docs/plans/2026-02-28-feat-advisor-feedback-loops-plan.md`
- [x] **Phase 5: Optional OTEL Backend Export** *(optional)* — send spans to any OTLP endpoint via `HOOKS_METRICS_OTLP_ENDPOINT` env var; direct OTLP/HTTP JSON (no SDK dependency). Synthetic session root spans with deterministic trace IDs. Correlates with Claude Code native telemetry via shared `claude.session_id`. Requires Phase 1. See plan: `docs/plans/idempotent-tumbling-sonnet.md`

## Parking Lot

Enhancements identified but out of scope for current work. Review before planning next iteration.

- [ ] **Homebrew distribution** — package as a Brew formula with an install script so it can be distributed without manual `rsync`/`install` commands
- [ ] **Install script** — single `install.sh` that handles Python dep check, deploys to `~/.claude/hooks/`, and patches `settings.json`
- [ ] **Sharpen fix suggestions in action items** — "Investigate hook failures" repeated 4x is not actionable; suggestions should be step-specific (e.g., include the actual script path for exit-127, the configured timeout for TIMEOUT items)
- [ ] **Filter performance table to problems only** — current table always shows 12 rows sorted by total_ms; rows with no timeout and normal latency (e.g., `eslint 2.2s (no limit)`) provide no signal and should be hidden unless explicitly requested
- [ ] **Suppress confirmation noise** — green traffic-light categories, FIXED badges, and GONE coverage gaps for test/internal steps are context, not action items; consider hiding or collapsing them
- [ ] **WoW summary table** — the 4-row aggregate (runs/failures/fail rate/overhead) adds context but no specific action; the trends section already highlights specific regressions; consider moving to `--verbose` only
- [ ] **TUI subtitle accuracy** — after returning from the Detail screen, the app subtitle stays as "Detail" until refresh; should restore dashboard subtitle on pop
- [ ] **Configurable thresholds** — `config.py` constants (regression %, timeout %, slow run threshold) are hardcoded; a user-level config file would allow tuning without editing source
- [ ] **audit-logger.sh: single `head` read** — currently reads `$TMPFILE` three times (`tool`, `session`, `full_payload`); consolidate into one `payload_head=$(head -c 65536 "$TMPFILE")` variable (PR #2 review comment)
- [x] **spans.py: replace slice unpacking with full destructure** — done in PR #2 fix-up commit
- [ ] **spans.py: add `__post_init__` validation to `Span`** — validate `len(trace_id)==32`, `len(span_id)==16`, `kind in (1,3)`, `status_code in (1,2)`, `start <= end` (PR #2 review comment)
- [ ] **spans.py: consider `IntEnum` for `SpanKind` and `StatusCode`** — replaces magic ints with self-documenting values, serializes to int naturally (PR #2 review comment)
- [ ] **spans.py / db.py: no test coverage** — factory functions, timestamp parsing, redaction logic, ID generation all untested; privacy-sensitive export pipeline warrants attention (PR #2 review comment)
- [x] **docstrings: remove phase-lifecycle references** — `spans_to_dict` "defer to Phase 5" reference removed; `_has_session_column` "added in Phase 1" reference remains for historical context (PR #2 review comment)
