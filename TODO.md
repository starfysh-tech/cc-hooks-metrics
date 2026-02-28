# TODO

## Prioritized

- [ ] **Reduce signal-to-noise in default output** — restructure output tiers: default shows traffic lights + grouped action items + failure/latency trends (~30-40 lines); verbose adds perf table, WoW summary, projects. Group action items by step so a broken step doesn't appear 5 times across sections. See plan: `.claude/plans/sprightly-singing-narwhal.md`

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
