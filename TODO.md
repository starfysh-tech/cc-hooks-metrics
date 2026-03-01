# TODO

## Prioritized

- [ ] **Reduce signal-to-noise in default output** — restructure output tiers: default shows traffic lights + grouped action items + failure/latency trends (~30-40 lines); verbose adds perf table, WoW summary, projects. Group action items by step so a broken step doesn't appear 5 times across sections. See plan: `.claude/plans/sprightly-singing-narwhal.md`

### OTEL Observability Roadmap

Five independently implementable phases. Each has a plan file in `docs/plans/`. Start with Phase 1 — all others depend on it or are independent. See roadmap: `.claude/plans/wise-dancing-raven.md`

- [ ] **Phase 1: Session Correlation + OTEL Span Model + Expanded Event Capture** — add `session` column to `hook_metrics`, widen audit-logger to `*` matcher, add `event-logger.sh` for 5 new event types, introduce `spans.py` with OTEL-shaped span model, add `--export-spans` CLI flag. See plan: `docs/plans/2026-02-28-feat-session-correlation-otel-spans-plan.md`
- [ ] **Phase 2: Local Analyses** — add per-step reliability (p50/p90/p99 + pain index), per-repo health profiles, and per-session summaries/timelines to `db.py`. Step/repo queries are Phase 1-independent; session queries require Phase 1 `session` column. See plan: `docs/plans/2026-02-28-feat-local-analyses-step-repo-session-plan.md`
- [ ] **Phase 3: TUI Screens + CLI Integration** — add `SessionsScreen` (press `s`) and `StepDrillScreen` (press `t`) to the TUI; add `--sessions` and `--step NAME` CLI flags; fix TUI subtitle bug on screen pop. Requires Phase 2 query layer. See plan: `docs/plans/2026-02-28-feat-tui-screens-sessions-steps-plan.md`
- [ ] **Phase 4: Advisor + Feedback Loops** — add `advisor.py` with guardrail tuning suggestions and periodic privacy-safe summaries; add `AdvisorScreen` (press `a`); add `--summary daily|weekly` and `--export-summary` CLI flags. Requires Phase 2 `step_reliability()`; hot sequences require Phase 1. See plan: `docs/plans/2026-02-28-feat-advisor-feedback-loops-plan.md`
- [ ] **Phase 5: Optional OTEL Backend Export** *(optional)* — send spans to any OTLP endpoint via `HOOKS_METRICS_OTLP_ENDPOINT` env var; `opentelemetry-sdk` is an optional dependency. Correlates with Claude Code native telemetry via shared `claude.session_id`. Requires Phase 1. See plan: `docs/plans/2026-02-28-feat-optional-otel-backend-export-plan.md`

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
