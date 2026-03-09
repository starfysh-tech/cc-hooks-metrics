# Stderr Capture + Coverage Gap Detection Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Capture stderr from failing hooks, surface the most common failure reason per step, and detect expected steps with no recent runs.

**Architecture:** Three-layer change — bash captures stderr into a new DB column, Python queries aggregate it by frequency, reports surface it inline on REGR/FAIL lines. Coverage gaps use a new `missing_expected_steps()` method against a config-driven `EXPECTED_STEPS` set (separate from the existing WoW-based `coverage_gaps()`).

**Tech Stack:** bash (hook-metrics.sh), SQLite (db-init.sh migration), Python 3.11 (db.py/config.py/static.py/tui.py/spans.py), pytest, Rich, Textual 8.x

---

### Task 1: Schema migration + test fixture update

**Files:**
- Modify: `db-init.sh`
- Modify: `tests/conftest.py`

**Step 1: Add migration to db-init.sh**

In `_init_hooks_db()`, after the existing `ALTER TABLE` migrations, add:

```bash
sqlite3 "$HOOKS_DB" "ALTER TABLE hook_metrics ADD COLUMN stderr_snippet TEXT DEFAULT ''" 2>/dev/null || true
```

The `|| true` pattern matches existing migrations — `ALTER TABLE` errors if column exists, which is expected on re-run.

**Step 2: Run against real DB to verify idempotency**

```bash
CLAUDE_HOOKS_DB=~/.claude/hooks.db bash -c 'source db-init.sh && _init_hooks_db && echo OK'
# Run twice — both must print OK
sqlite3 ~/.claude/hooks.db ".schema hook_metrics" | grep stderr_snippet
# Expected: stderr_snippet TEXT DEFAULT ''
```

**Step 3: Update test schema in conftest.py**

In `test_db_path` fixture (line ~14), add `stderr_snippet TEXT DEFAULT ''` to the `hook_metrics` CREATE TABLE:

```python
CREATE TABLE hook_metrics (
    id INTEGER PRIMARY KEY,
    ts TEXT DEFAULT (datetime('now')),
    hook TEXT,
    step TEXT,
    duration_ms INTEGER,
    exit_code INTEGER,
    repo TEXT DEFAULT '',
    cmd TEXT DEFAULT '',
    session TEXT DEFAULT '',
    stderr_snippet TEXT DEFAULT ''
);
```

**Step 4: Update `seed_hook_metrics_ext` in conftest.py**

Extend the dict-based seeder to support `stderr_snippet` (default `''`):

```python
conn.execute(
    "INSERT INTO hook_metrics (hook, step, duration_ms, exit_code, repo, session, cmd, ts, stderr_snippet)"
    " VALUES (?,?,?,?,?,?,?,?,?)",
    (r["hook"], r["step"], r["duration_ms"], r["exit_code"],
     r["repo"], r["session"], r.get("cmd", ""), r["ts"], r.get("stderr_snippet", "")),
)
# And in the no-ts branch:
"INSERT INTO hook_metrics (hook, step, duration_ms, exit_code, repo, session, cmd, stderr_snippet)"
" VALUES (?,?,?,?,?,?,?,?)",
(..., r.get("stderr_snippet", "")),
```

**Step 5: Run existing tests to confirm no regressions**

```bash
cd /Users/randallnoval/Code/cc-hooks-metrics
python -m pytest tests/ -x -q
# Expected: all passing
```

**Step 6: Commit**

```bash
git add db-init.sh tests/conftest.py
git commit -m "feat: add stderr_snippet column to hook_metrics schema and test fixtures"
```

---

### Task 2: Capture stderr in hook-metrics.sh

**Files:**
- Modify: `hook-metrics.sh`

The script currently runs the wrapped command at line 45:
```bash
/usr/bin/time -p -o "$time_file" "$@" < "$input_file"
exit_code=$?
```

**Step 1: Add stderr temp file**

After `time_file=$(mktemp)` (line 37), add:
```bash
stderr_file=$(mktemp)
```

Update the trap to clean it up:
```bash
trap 'rm -f "$input_file" "$time_file" "$stderr_file"' EXIT
```

**Step 2: Redirect stderr of wrapped command**

Change the command execution (line 45) to tee stderr to the temp file:
```bash
/usr/bin/time -p -o "$time_file" "$@" < "$input_file" 2>"$stderr_file"
exit_code=$?
```

Note: `/usr/bin/time` writes its timing output to stderr — but with `-o "$time_file"` flag, timing goes to the file, not stderr. The `2>"$stderr_file"` captures only the wrapped script's stderr.

**Step 3: Capture snippet on non-zero exit**

After `exit_code=$?`, add:
```bash
# Capture stderr snippet only on failure (empty string on success = no overhead)
stderr_snippet=""
if [ "$exit_code" -ne 0 ]; then
  stderr_snippet=$(head -c 200 "$stderr_file" | tr '\n\r\t' '   ' | tr -d '`$')
fi
```

**Step 4: Add to DB insert**

Update the INSERT statement to include the new column:
```sql
INSERT INTO hook_metrics (ts, hook, step, cmd, exit_code, duration_ms, real_s, user_s, sys_s, branch, sha, host, repo, session, stderr_snippet)
VALUES (
  '$(_sql_escape "$ts")',
  ...existing fields...,
  '$(_sql_escape "$SESSION_ID")',
  '$(_sql_escape "$stderr_snippet")'
);
```

**Step 5: Manual smoke test**

```bash
# Simulate a failing hook and verify stderr_snippet is captured
echo '{}' | bash hook-metrics.sh PostToolUse:test-stderr bash -c 'echo "test error msg" >&2; exit 1' || true
sqlite3 ~/.claude/hooks.db \
  "SELECT step, exit_code, stderr_snippet FROM hook_metrics WHERE step='test-stderr' ORDER BY id DESC LIMIT 1;"
# Expected: test-stderr|1|test error msg
```

**Step 6: Commit**

```bash
git add hook-metrics.sh
git commit -m "feat: capture stderr snippet from failing hooks into hook_metrics"
```

---

### Task 3: config.py — EXPECTED_STEPS + EXIT_CODE_LABELS

**Files:**
- Modify: `hooks_report/config.py`

**Step 1: Write the failing test**

Create `tests/test_config_expected_steps.py`:

```python
from hooks_report import config


def test_expected_steps_derived_from_step_timeouts():
    """EXPECTED_STEPS must be a subset of STEP_TIMEOUTS keys."""
    assert config.EXPECTED_STEPS.issubset(set(config.STEP_TIMEOUTS.keys()))


def test_expected_steps_excludes_skip_pattern():
    """No step matching SKIP_HOOKS_PATTERN should appear in EXPECTED_STEPS."""
    import re
    for step in config.EXPECTED_STEPS:
        assert not re.fullmatch(config.SKIP_HOOKS_PATTERN, step), (
            f"step '{step}' matches SKIP_HOOKS_PATTERN but is in EXPECTED_STEPS"
        )


def test_exit_code_labels_has_known_codes():
    assert 127 in config.EXIT_CODE_LABELS
    assert 124 in config.EXIT_CODE_LABELS
    assert 141 in config.EXIT_CODE_LABELS
```

**Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_config_expected_steps.py -v
# Expected: FAIL — EXPECTED_STEPS not defined
```

**Step 3: Add to config.py**

After `GUARDRAIL_STEPS` line, add:

```python
# Steps expected to run regularly — used for coverage gap detection
# Derived from STEP_TIMEOUTS so there's only one list to maintain
EXPECTED_STEPS: set[str] = set(STEP_TIMEOUTS.keys())

EXIT_CODE_LABELS: dict[int, str] = {
    127: "binary not found",
    124: "timeout",
    141: "SIGPIPE",
    2: "guardrail block",
}
```

**Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_config_expected_steps.py -v
# Expected: PASS
```

**Step 5: Commit**

```bash
git add hooks_report/config.py tests/test_config_expected_steps.py
git commit -m "feat: add EXPECTED_STEPS and EXIT_CODE_LABELS to config"
```

---

### Task 4: db.py — top_failure_reasons() method

**Files:**
- Modify: `hooks_report/db.py`
- Modify: `tests/test_reporting.py`

This method returns the top N most-common `stderr_snippet` values for a given step, aggregated across all sessions/repos. Used for workflow-level pattern detection, not incident debugging.

**Step 1: Write the failing test**

In `tests/test_reporting.py`, add:

```python
def test_top_failure_reasons_returns_most_common(db, test_db_path):
    seed_hook_metrics_ext(test_db_path, [
        {"hook": "PostToolUse", "step": "audit-logger", "duration_ms": 50,
         "exit_code": 5, "repo": "r1", "session": "s1", "stderr_snippet": "jq: parse error"},
        {"hook": "PostToolUse", "step": "audit-logger", "duration_ms": 50,
         "exit_code": 5, "repo": "r2", "session": "s2", "stderr_snippet": "jq: parse error"},
        {"hook": "PostToolUse", "step": "audit-logger", "duration_ms": 50,
         "exit_code": 1, "repo": "r3", "session": "s3", "stderr_snippet": "db locked"},
    ])
    reasons = db.top_failure_reasons("audit-logger")
    assert len(reasons) >= 1
    assert reasons[0].snippet == "jq: parse error"
    assert reasons[0].count == 2


def test_top_failure_reasons_excludes_exit0(db, test_db_path):
    seed_hook_metrics_ext(test_db_path, [
        {"hook": "PostToolUse", "step": "audit-logger", "duration_ms": 50,
         "exit_code": 0, "repo": "r1", "session": "s1", "stderr_snippet": "should not appear"},
    ])
    reasons = db.top_failure_reasons("audit-logger")
    assert reasons == []


def test_top_failure_reasons_empty_snippet_grouped(db, test_db_path):
    """Empty stderr_snippet on non-zero exit returns entry with empty string."""
    seed_hook_metrics_ext(test_db_path, [
        {"hook": "PostToolUse", "step": "audit-logger", "duration_ms": 50,
         "exit_code": 5, "repo": "r1", "session": "s1", "stderr_snippet": ""},
        {"hook": "PostToolUse", "step": "audit-logger", "duration_ms": 50,
         "exit_code": 5, "repo": "r2", "session": "s2", "stderr_snippet": ""},
    ])
    reasons = db.top_failure_reasons("audit-logger")
    assert len(reasons) == 1
    assert reasons[0].snippet == ""
    assert reasons[0].count == 2
```

**Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_reporting.py::test_top_failure_reasons_returns_most_common -v
# Expected: FAIL — top_failure_reasons not defined
```

**Step 3: Add dataclass + method to db.py**

After the existing dataclasses (near `FailureTrend`, around line 100), add:

```python
@dataclass
class FailureReason:
    snippet: str
    count: int
    exit_code: int | None
```

In the `HooksDB` class, after `coverage_gaps()` (line ~582), add:

```python
def top_failure_reasons(self, step: str, days: int = 7, limit: int = 5) -> list[FailureReason]:
    """Most common stderr_snippet values for a step on non-zero exits."""
    rows = self._query("""
SELECT stderr_snippet, COUNT(*) AS cnt, exit_code
FROM hook_metrics
WHERE step = ? AND exit_code != 0 AND ts > datetime('now', ? || ' days')
GROUP BY stderr_snippet, exit_code
ORDER BY cnt DESC
LIMIT ?
""", (step, f"-{days}", limit))
    return [FailureReason(snippet=str(s or ""), count=_int(c), exit_code=_int(ec))
            for s, c, ec in rows]
```

**Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_reporting.py::test_top_failure_reasons_returns_most_common \
  tests/test_reporting.py::test_top_failure_reasons_excludes_exit0 \
  tests/test_reporting.py::test_top_failure_reasons_empty_snippet_grouped -v
# Expected: PASS
```

**Step 5: Commit**

```bash
git add hooks_report/db.py tests/test_reporting.py
git commit -m "feat: add top_failure_reasons() to HooksDB"
```

---

### Task 5: db.py — missing_expected_steps() method

**Files:**
- Modify: `hooks_report/db.py`
- Modify: `tests/test_reporting.py`

This is distinct from the existing `coverage_gaps()` (WoW disappearance). `missing_expected_steps()` checks steps in `EXPECTED_STEPS` with zero runs in the window.

**Step 1: Write the failing test**

In `tests/test_reporting.py`, add:

```python
def test_missing_expected_steps_returns_absent_steps(db, test_db_path, monkeypatch):
    import hooks_report.config as cfg
    monkeypatch.setattr(cfg, "EXPECTED_STEPS", {"audit-logger", "guard-security", "phi-check"})
    # Only audit-logger has runs
    seed_hook_metrics(test_db_path, [
        ("PostToolUse", "audit-logger", 50, 0, "r1", "s1"),
    ])
    missing = db.missing_expected_steps(days=7)
    assert "guard-security" in missing
    assert "phi-check" in missing
    assert "audit-logger" not in missing


def test_missing_expected_steps_empty_when_all_present(db, test_db_path, monkeypatch):
    import hooks_report.config as cfg
    monkeypatch.setattr(cfg, "EXPECTED_STEPS", {"audit-logger"})
    seed_hook_metrics(test_db_path, [
        ("PostToolUse", "audit-logger", 50, 0, "r1", "s1"),
    ])
    assert db.missing_expected_steps(days=7) == []
```

**Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_reporting.py::test_missing_expected_steps_returns_absent_steps -v
# Expected: FAIL
```

**Step 3: Add method to db.py**

After `top_failure_reasons()`:

```python
def missing_expected_steps(self, days: int = 7) -> list[str]:
    """Return EXPECTED_STEPS not seen in hook_metrics within the window."""
    seen = {
        row[0]
        for row in self._query(
            "SELECT DISTINCT step FROM hook_metrics WHERE ts > datetime('now', ? || ' days')",
            (f"-{days}",),
        )
    }
    skip = config.SKIP_HOOKS_PATTERN
    return sorted(
        s for s in config.EXPECTED_STEPS
        if s not in seen and not re.fullmatch(skip, s)
    )
```

**Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_reporting.py::test_missing_expected_steps_returns_absent_steps \
  tests/test_reporting.py::test_missing_expected_steps_empty_when_all_present -v
# Expected: PASS
```

**Step 5: Commit**

```bash
git add hooks_report/db.py tests/test_reporting.py
git commit -m "feat: add missing_expected_steps() to HooksDB"
```

---

### Task 6: db.py — enrich action_items() with top error + MISSING items

**Files:**
- Modify: `hooks_report/db.py`
- Modify: `tests/test_reporting.py`

**Step 1: Write the failing test**

```python
def test_action_items_fail_includes_top_error(db, test_db_path):
    """FAIL action items include top_error when stderr_snippet is populated."""
    seed_hook_metrics_ext(test_db_path, [
        {"hook": "PostToolUse", "step": "audit-logger", "duration_ms": 50,
         "exit_code": 5, "repo": "r1", "session": "s1", "stderr_snippet": "jq: parse error"},
        {"hook": "PostToolUse", "step": "audit-logger", "duration_ms": 50,
         "exit_code": 5, "repo": "r2", "session": "s2", "stderr_snippet": "jq: parse error"},
    ])
    items = db.action_items()
    fail_items = [i for i in items if i.category == "FAIL" and i.step == "audit-logger"]
    assert len(fail_items) == 1
    assert "jq: parse error" in fail_items[0].detail


def test_action_items_missing_step_appears(db, monkeypatch):
    """MISSING action items appear for EXPECTED_STEPS with no runs."""
    import hooks_report.config as cfg
    monkeypatch.setattr(cfg, "EXPECTED_STEPS", {"never-ran-step"})
    items = db.action_items()
    missing = [i for i in items if i.category == "MISSING"]
    assert any(i.step == "never-ran-step" for i in missing)
```

**Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_reporting.py::test_action_items_fail_includes_top_error \
  tests/test_reporting.py::test_action_items_missing_step_appears -v
# Expected: FAIL
```

**Step 3: Update `action_items()` in db.py**

In the reliability failures block (around line 722), enrich the detail string:

```python
for step, count in fail_rows:
    cnt = _int(count)
    word = "failure" if cnt == 1 else "failures"
    # Fetch top error for attribution
    reasons = self.top_failure_reasons(step)
    top_error = ""
    if reasons and reasons[0].snippet:
        r = reasons[0]
        code_label = config.EXIT_CODE_LABELS.get(r.exit_code, f"exit {r.exit_code}")
        top_error = f" [{code_label}: \"{r.snippet[:60]}\" ×{r.count}]"
    items.append(ActionItem(
        category="FAIL", severity="red", step=step,
        detail=f"{step} — {cnt} {word} (24h){top_error}",
        fix="Investigate hook failures",
    ))
```

After the reliability failures block, before `return items`, add:

```python
# Missing expected steps
for step in self.missing_expected_steps():
    items.append(ActionItem(
        category="MISSING", severity="yellow", step=step,
        detail=f"{step} — no runs in 7d (expected)",
        fix="Verify hook is wired in settings.json",
    ))
```

**Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_reporting.py::test_action_items_fail_includes_top_error \
  tests/test_reporting.py::test_action_items_missing_step_appears -v
# Expected: PASS
```

**Step 5: Run full test suite**

```bash
python -m pytest tests/ -x -q
# Expected: all passing
```

**Step 6: Commit**

```bash
git add hooks_report/db.py tests/test_reporting.py
git commit -m "feat: enrich action_items with top failure reason and missing expected steps"
```

---

### Task 7: static.py — REGR lines with top error + [MISSING] trend lines

**Files:**
- Modify: `hooks_report/static.py`

No new tests needed — rendering is tested visually. Verify by running `--static` after changes.

**Step 1: Update REGR trend lines in `section_wow_compact()` (around line 219)**

After the bar chart, append top error for each regression step:

```python
for r in regressions:
    line = Text()
    line.append_text(render.trend_badge("REGR"))
    line.append(f"  {r.step:<22}  ")
    line.append_text(render.bar_chart(r.cur_f, max_fail, 14, "red"))
    line.append(f"  {r.cur_f:4d} fail  (was {r.prev_f}, ")
    line.append_text(render.pct_change(r.cur_f, r.prev_f, "lower_better"))
    line.append(")")
    # Top failure reason
    reasons = db.top_failure_reasons(r.step)
    if reasons and reasons[0].snippet:
        top = reasons[0]
        code_label = config.EXIT_CODE_LABELS.get(top.exit_code, f"exit {top.exit_code}")
        line.append(f'  top: {code_label} "{top.snippet[:50]}" ×{top.count}', style="dim")
    console.print(line)
```

Note: `section_wow_compact()` needs `db` passed in — check its current signature and add `db: HooksDB` param if missing. Check callers in `static.py` to pass it through.

**Step 2: Add [MISSING] trend lines**

After the coverage gaps block (around line 262), add:

```python
# Missing expected steps
missing_steps = db.missing_expected_steps()
for step in missing_steps:
    line = Text()
    line.append_text(render.trend_badge("MISSING"))
    line.append(f"  {step:<22}  — no runs in 7d (expected)")
    console.print(line)
```

**Step 3: Add "MISSING" to `trend_badge()` in render.py**

In `render.py`, `trend_badge()` styles dict (line ~34):
```python
"MISSING": ("yellow", "[MISSING]"),
```

**Step 4: Verify section_wow_compact signature**

Check where `section_wow_compact` is called (in `section_compact` and `section_verbose`) and ensure `db` is available in scope. It's called as `section_wow_compact(console, db, verbose=verbose)` — confirm `db` is already a parameter.

**Step 5: Smoke test**

```bash
./hooks-report.sh --static
# Verify: REGR lines show "top: ..." suffix for steps with stderr_snippet data
# Verify: [MISSING] lines appear for any EXPECTED_STEPS not seen in 7d
```

**Step 6: Commit**

```bash
git add hooks_report/static.py hooks_report/render.py
git commit -m "feat: show top failure reason on REGR lines and add [MISSING] trend lines"
```

---

### Task 8: tui.py — Top Failure Reasons panel in StepDrillScreen

**Files:**
- Modify: `hooks_report/tui.py`

**Step 1: Add widgets to compose()**

In `StepDrillScreen.compose()` (line 225), add two new Static widgets after guardrail widgets:

```python
yield Static(id="failure-reasons-header")
yield Static(id="failure-reasons-table")
```

**Step 2: Populate in on_mount()**

After the guardrail block in `on_mount()`, add:

```python
try:
    # Build top failure reasons across all non-zero steps
    failing_steps = [
        row[0] for row in db._query(
            "SELECT DISTINCT step FROM hook_metrics "
            "WHERE exit_code != 0 AND ts > datetime('now', '-7 days') "
            "ORDER BY step"
        )
    ]
    if failing_steps:
        self.query_one("#failure-reasons-header", Static).update(
            Text(f"\n  Top Failure Reasons (last 7d)", style="bold")
        )
        table = Table(box=box.SIMPLE, padding=(0, 1))
        table.add_column("Step", style="cyan", no_wrap=True)
        table.add_column("Exit", justify="right")
        table.add_column("Count", justify="right")
        table.add_column("Most Common Error", style="dim")
        for step in failing_steps:
            reasons = db.top_failure_reasons(step, limit=1)
            if reasons:
                r = reasons[0]
                code_label = config.EXIT_CODE_LABELS.get(r.exit_code, str(r.exit_code))
                snippet = r.snippet[:60] if r.snippet else "(no stderr)"
                table.add_row(step, code_label, str(r.count), snippet)
        self.query_one("#failure-reasons-table", Static).update(table)
except HooksDBError as e:
    self.query_one("#failure-reasons-header", Static).update(
        Text(f"\n  Top Failure Reasons — DB error: {e}", style="red")
    )
```

Check imports at top of `tui.py` — add `from rich import box` if not present, and `from . import config`.

**Step 3: Smoke test TUI**

```bash
./hooks-report.sh
# Press 't' to open StepDrillScreen
# Verify: "Top Failure Reasons" table appears at bottom
# Verify: steps with no stderr_snippet show "(no stderr)"
```

**Step 4: Commit**

```bash
git add hooks_report/tui.py
git commit -m "feat: add Top Failure Reasons panel to StepDrillScreen"
```

---

### Task 9: spans.py — stderr_snippet as span attribute

**Files:**
- Modify: `hooks_report/spans.py`
- Modify: `tests/test_otlp.py` (or relevant span test)

**Step 1: Find where hook span attributes are built**

In `spans.py`, find `hook_metric_to_span()` — it reads from the `hook_metrics` row tuple. The `spans_raw()` query in `db.py` (line ~1485) selects specific columns — add `stderr_snippet` to that SELECT.

**Step 2: Update `spans_raw()` in db.py**

Find `spans_raw()` (line ~1485) and add `stderr_snippet` to the SELECT list. Ensure the column index is tracked.

**Step 3: Update `hook_metric_to_span()` in spans.py**

Add `hook.stderr_snippet` attribute only when non-empty and `include_sensitive=True` (or always — it's operational data, not sensitive). Follow the existing `include_sensitive` pattern for the field.

```python
if row.stderr_snippet:
    attrs["hook.stderr_snippet"] = row.stderr_snippet
```

**Step 4: Write a test**

In `tests/test_otlp.py`, add a test that seeds a row with `stderr_snippet` set and verifies the attribute appears in the span output.

**Step 5: Run tests**

```bash
python -m pytest tests/test_otlp.py -v
# Expected: PASS
```

**Step 6: Run full test suite + smoke test export**

```bash
python -m pytest tests/ -q
./hooks-report.sh --export | python3 -c "import sys,json; d=json.load(sys.stdin); print('spans:', len(d))"
```

**Step 7: Deploy and commit**

```bash
rsync -a --delete hooks_report/ ~/.claude/hooks/hooks_report/
install -m 755 hooks-report.sh ~/.claude/hooks/hooks-report.sh
install -m 755 hook-metrics.sh ~/.claude/hooks/hook-metrics.sh

git add hooks_report/spans.py hooks_report/db.py tests/test_otlp.py
git commit -m "feat: add hook.stderr_snippet attribute to OTel spans"
```

---

### Task 10: Final integration test + deploy

**Step 1: Run full test suite**

```bash
python -m pytest tests/ -v
# Expected: all passing
```

**Step 2: Run static report**

```bash
./hooks-report.sh --static
# Verify: REGR lines show top error
# Verify: [MISSING] lines for any gap steps
# Verify: action items for FAIL show attribution
# Verify: action items for MISSING show expected steps
```

**Step 3: Run export**

```bash
./hooks-report.sh --export | python3 -m json.tool | grep -c "hook.stderr"
# Expected: > 0 if any failures with stderr data exist
```

**Step 4: Deploy all changed files**

```bash
rsync -a --delete hooks_report/ ~/.claude/hooks/hooks_report/
install -m 755 hooks-report.sh ~/.claude/hooks/hooks-report.sh
install -m 755 hook-metrics.sh ~/.claude/hooks/hook-metrics.sh
install -m 755 db-init.sh ~/.claude/hooks/db-init.sh
```

**Step 5: Final commit**

```bash
git add -p  # stage any remaining changes
git commit -m "chore: deploy stderr capture + coverage gap detection"
```
