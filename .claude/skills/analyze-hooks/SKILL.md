---
name: analyze-hooks
description: Analyze Claude Code hook metrics to produce prioritized, implementable insights. Use when the user asks to analyze hooks, review hook health, check hook performance, wants to know what to fix next, says "analyze my hooks", "what should I fix", "hook insights", or anything about understanding or improving their hook setup. Also trigger when the user runs /analyze-hooks.
---

# Analyze Hooks

You are an analyst for Claude Code hook telemetry. Your job is to turn raw metrics into a prioritized brief with **specific, implementable proposals** — not vague suggestions.

## Step 0: Check Data Completeness

Before analyzing, verify you're seeing ALL the data. There are two metrics systems:

1. **Claude Code hooks** → `hook-metrics.sh` → `hooks.db` (SQLite) — the primary data source
2. **Git hooks (husky)** → `hooktime` → `~/.claude/hook-metrics.log` (JSONL) — may contain steps invisible to the report

Run the JSONL import to sync both systems before analysis:
```bash
~/.claude/hooks/jsonl-import.sh 2>&1
```

If the import adds rows, the report output will differ from previous runs. This is expected.

Also check for steps that appear in one system but not the other:
```bash
# Steps in JSONL but not in recent SQLite data
sqlite3 ~/.claude/hooks.db "SELECT DISTINCT step FROM hook_metrics WHERE hook NOT IN ('PreToolUse','PostToolUse','PermissionRequest','Stop','Notification','SessionStart','SessionEnd','SubagentStart','SubagentStop','PostToolUseFailure','UserPromptSubmit') AND ts > datetime('now','-7 days')"
```

If a step shows as `[MISSING]` in the report but exists in JSONL or in a project's `.husky/` hooks, it's a data pipeline gap — not a broken hook.

## Step 1: Collect Data

Run the full export to get structured JSON:

```bash
~/.claude/hooks/hooks-report.sh --export 2>/dev/null
```

Also run verbose for the sections not yet in the export (step reliability, repo profiles, sessions, failure reasons, advisor):

```bash
~/.claude/hooks/hooks-report.sh --verbose 2>&1
```

Parse both. The JSON is your primary data source; the verbose text fills gaps (percentiles, repo breakdowns, session overhead, hot sequences).

## Step 2: Investigate Before Claiming

The report surfaces symptoms. Your job is to diagnose causes before proposing fixes. For each issue:

1. **Read the relevant source code** — don't propose changes to files you haven't read
2. **Check the DB for context** — failure reasons, timing patterns, exit codes tell you what's actually happening
3. **Trace the wiring** — a "missing" step might be wired differently than expected (project hooks, husky, different event name). Check `~/.claude/settings.json`, project `.claude/settings.json` files, and `.husky/` directories
4. **Distinguish environmental from code issues** — if 10+ steps all regressed similarly, check `uptime`, system load, and recent OS updates before proposing per-step fixes
5. **Check step classification** — before counting exit-2 as a failure, check if the step belongs in `GUARDRAIL_STEPS` (exit 2 = intentional block, not failure). Before counting exit-1 as a failure, check if it belongs in `SEMANTIC_EXIT_STEPS` (exit 1 = findings, not failure). Steps like test runners (pytest, vitest) and code reviewers use exit 1 semantically.
6. **Check for missing timeouts** — newly imported husky steps (codex-review, pytest, vitest, eslint, tsc, etc.) often lack entries in `STEP_TIMEOUTS`. Propose timeouts based on observed p99 with headroom.

Useful diagnostic queries:
```bash
# Failure reasons for a specific step
sqlite3 ~/.claude/hooks.db "SELECT exit_code, substr(stderr_snippet,1,100), COUNT(*) FROM hook_metrics WHERE step='STEP_NAME' AND exit_code <> 0 AND ts > datetime('now','-30 days') GROUP BY exit_code, substr(stderr_snippet,1,100) ORDER BY COUNT(*) DESC LIMIT 10"

# Check when failures last occurred (might be historical, not current)
sqlite3 ~/.claude/hooks.db "SELECT step, exit_code, COUNT(*), MAX(ts), MIN(ts) FROM hook_metrics WHERE step='STEP_NAME' AND exit_code <> 0 GROUP BY exit_code"

# Guardrail block reasons
sqlite3 ~/.claude/hooks.db "SELECT substr(stderr_snippet,1,200), COUNT(*) FROM hook_metrics WHERE step='GUARD_NAME' AND exit_code=2 AND ts > datetime('now','-7 days') GROUP BY substr(stderr_snippet,1,200) ORDER BY COUNT(*) DESC LIMIT 10"

# System load context for latency regressions
uptime
```

## Step 3: Cross-Correlate

Don't just list what the report already shows — that's what `--static` is for. Instead, synthesize across signals:

### Impact Ranking
For every issue, compute **real impact** using `runs × avg_ms / 60000 = min/week overhead`. A step with 50% regression but 4 runs/week matters less than one with 20% regression and 17,000 runs/week. Lead with highest-impact items. Show the math — "170 runs × 85s avg = 240 min/week" is more convincing than "codex-review is slow."

### Systemic Detection
When 3+ steps all regressed in the same direction (latency up, failures up) in the same period, flag it as likely systemic (machine change, OS update, dependency upgrade) rather than 3 independent issues. Check if the affected steps share a common dependency (all Python-based, all shell-based, all touching the same DB).

### Timeout vs Optimization
When a step's max duration exceeds its timeout but avg is fine, the issue is tail latency (spikes), not baseline performance. The fix is usually a higher timeout, not optimization. When avg is high AND max is high, the step itself is slow and needs optimization.

### Guardrail Effectiveness
Cross-reference guardrail block rates with the step's value. A guardrail blocking 6.5% of the time might be too strict (noisy) or appropriately catching real issues. Query the actual block reasons (stderr_snippet) to determine which — don't just say "investigate."

### Overhead Concentration
Identify if overhead is concentrated in one repo or spread evenly. If 87% of overhead comes from one project, the optimization strategy is different than if it's spread across 10 repos.

### Silent Issues
Look for things the report flags as "green" that might still deserve attention:
- Steps with 0% failure but very high p99 (reliability looks fine, but tail latency is bad)
- Steps with no timeout configured and high max durations
- Steps that stopped running (GONE) that maybe shouldn't have
- New steps (NEW) that might not have timeouts configured yet

## Step 4: Produce the Brief

Structure your output as:

### Header
One-line health summary. Be honest — if things are mostly fine, say so. Don't manufacture urgency.

### Overview Table
Right after the header, show a compact summary table so the user can scan before reading details:

```markdown
| Step | Overhead | Status |
|------|----------|--------|
| codex-review | 178 min/wk (54%) | improving -17% WoW |
| pytest | 52 min/wk (16%) | timeout configured |
| eslint | 10 min/wk (3%) | outlier 464s spike |
| guard-python-lint | 2 min/wk | 6.1% block rate, healthy |
```

Include the top 5-8 steps by overhead. The "Status" column should use one of these categories: trend (e.g., "improving -17%"), anomaly (e.g., "outlier 464s"), config state (e.g., "timeout configured"), or metric (e.g., "6.1% block rate").

### Top 3 Actions
Before the full findings list, give a 3-line executive summary: the top 3 things the user should do, each one sentence. This lets them act immediately without reading the full brief. Example:
1. Raise codex-review timeout to 300s (clears false alarm, 240 min/week overhead is expected)
2. Add timeouts for pytest (600s), eslint (30s), vitest (120s) — all unbound husky steps
3. No action on latency regressions — systemic from load avg 14, will resolve when load drops

### Findings (prioritized by impact)
Use severity markers in finding headers:
- `#### 🔴 1. ...` — requires action, real impact
- `#### 🟡 2. ...` — worth monitoring, no immediate action
- `#### 🟢 3. ...` — non-obvious healthy signal worth calling out (don't duplicate "What's Working Well")

For each finding:
- **What**: One sentence describing the issue
- **Evidence**: The specific numbers from the data
- **Root cause**: What you found when you investigated (not speculation — if you couldn't determine it, say so)
- **Impact**: Quantified (e.g., "adds ~X seconds/week of overhead" or "blocks Y% of runs unnecessarily")
- **Proposal**: A specific, implementable change with file path and line reference. Not "investigate" — instead:
  - "Change `STEP_TIMEOUTS['audit-logger']` from 5000 to 15000 in `config.py:5`"
  - "The 193 exit-127 failures are from `scripts/no-verify-gate.sh` — last occurred Feb 27, step is no longer wired in settings.json, remove from STEP_TIMEOUTS"
  - "guard-python-lint blocks 6.5% — 23% are real bugs (F821 undefined names), 47% are auto-fixable style (F401, I001). Keep as-is; block rate is healthy"

### What's Working Well
Brief system-level healthy signals (e.g., "overall failure rate 0.004%", "overhead down 12% WoW", "no broken hooks"). Keep this to 3-5 bullet points. Step-level health belongs in the Overview Table's Status column, not here.

### Proposals Summary
Collect all proposals from the findings above into a single table for quick reference and "do 1 and 3" interaction:

```markdown
| # | Change | File | Impact |
|---|--------|------|--------|
| 1 | codex-review timeout 120→360s | config.py:6 | clears false alarm |
| 2 | Add pytest timeout 600s | config.py | caps runaways |
```

The user can say "do 1 and 3" and you implement them.

## Step 5: Implement on Approval

When the user approves a proposal (or says "do it", "go ahead", "implement 1 and 3", etc.):

1. Read the relevant source files before making changes
2. Make the change in the **repo source** (`/Users/randallnoval/Code/cc-hooks-metrics/`), not the deployed copy
3. If the change is to config values (timeouts, thresholds), verify by re-running `--static` to confirm the traffic lights updated
4. If the change is to hook scripts, test with a dry run if possible
5. Remind the user to run `install.sh` to deploy changes

## Key Files

These are the files you'll most commonly need to modify:

- `/Users/randallnoval/Code/cc-hooks-metrics/hooks_report/config.py` — timeouts, thresholds, step lists (source of truth)
- `/Users/randallnoval/Code/cc-hooks-metrics/hooks_report/db.py` — database queries
- `~/.claude/settings.json` — Claude Code hook wiring
- Project `.claude/settings.json` and `.husky/` — project-level hook wiring
- `/Users/randallnoval/Code/cc-hooks-metrics/*.sh` — hook scripts (source of truth)
- `~/.claude/hooks/` — deployed copies (modified by `install.sh`, not directly)

## Important Context

- `codex-review` uses semantic exit codes (exit 1 = findings, not failure) — it's excluded from failure counts via `SEMANTIC_EXIT_STEPS` in config.py
- `SKIP_HOOKS_PATTERN` in config.py filters test/noise steps from coverage gap detection — use `re.fullmatch()` not `re.search()`
- The DB is at `~/.claude/hooks.db` (overridable via `CLAUDE_HOOKS_DB`)
- JSONL log is at `~/.claude/hook-metrics.log` — written by `hooktime` (husky git hooks)
- Hook scripts are deployed from the `cc-hooks-metrics` repo via `install.sh`
- Changes to source files in the repo need to be deployed via `install.sh` to take effect
- `EXIT_CODE_LABELS` in config.py maps exit codes to human-readable names — check it before claiming an exit code is "unknown"
