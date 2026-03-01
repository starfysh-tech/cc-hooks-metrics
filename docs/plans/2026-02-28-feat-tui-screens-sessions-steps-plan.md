---
title: "Phase 3: TUI Screens + CLI Integration"
phase: 3
date: 2026-02-28
dependencies: "Phase 2 (dataclasses and query methods)"
status: ready
---

# Phase 3: TUI Screens + CLI Integration

## Overview

Phase 3 surfaces the Phase 2 analyses through two new TUI screens (`SessionsScreen`, `StepDrillScreen`), four new render helpers, three new static output sections, and two new CLI arguments. It also fixes the known TUI subtitle bug where the subtitle stays "Detail" after popping back to the dashboard.

## Dependencies

- **Phase 2 required**: `StepReliability`, `RepoProfile`, `SessionSummary`, `SessionTimeline` dataclasses and their query methods (`step_reliability()`, `step_drilldown()`, `repo_profiles()`, `session_list()`, `session_timeline()`)
- **Phase 1 partial**: `SessionsScreen` displays a fallback message when `_has_session_column()` returns `False` (graceful degradation, not a hard blocker)
- **No changes to `db.py`**: Phase 3 is purely presentation layer

## Implementation

### 3a. New file: `hooks_report/screens.py`

Create `hooks_report/screens.py` (~120 lines). Two new `Screen` subclasses following the existing `DetailScreen` pattern in `tui.py` (line 99).

#### `SessionsScreen`

```python
from __future__ import annotations

from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import ScrollableContainer
from textual.screen import Screen
from textual.widgets import Footer, Header, Static

from . import render
from .db import HooksDB


class SessionsScreen(Screen):
    """Session analysis: worst sessions + selected session timeline."""

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("q", "app.quit", "Quit"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        with ScrollableContainer():
            yield Static(id="sessions-header")
            yield Static(id="sessions-table")
            yield Static(id="sessions-note")
        yield Footer()

    def on_mount(self) -> None:
        db: HooksDB = self.app.db  # type: ignore[attr-defined]
        self.app.sub_title = "Sessions"

        if not db._has_session_column():
            self.query_one("#sessions-header", Static).update(
                Text("\n  Sessions — Phase 1 migration required", style="bold yellow")
            )
            self.query_one("#sessions-note", Static).update(
                Text(
                    "  The session column has not been added to hook_metrics yet.\n"
                    "  Deploy Phase 1 (session correlation) to enable this screen.\n",
                    style="dim",
                )
            )
            return

        self.query_one("#sessions-header", Static).update(
            Text("\n  Worst Sessions (last 7d, by failures)", style="bold")
        )

        sessions = db.session_list(days=7, limit=15)
        if not sessions:
            self.query_one("#sessions-table", Static).update(
                Text("  No session data found.", style="dim")
            )
            return

        self.query_one("#sessions-table", Static).update(
            render.session_table(sessions)
        )

        # Summary note
        total_failures = sum(s.hook_failures for s in sessions)
        total_overhead = sum(s.overhead_ms for s in sessions)
        note = Text(f"\n  Showing {len(sessions)} sessions | ")
        note.append(f"{total_failures} total failures | ")
        note.append(f"{total_overhead / 1000:.1f}s total overhead")
        self.query_one("#sessions-note", Static).update(note)
```

Notes:
- Follows `DetailScreen` pattern exactly: `compose()` yields `Header` + `ScrollableContainer` + `Footer`
- `on_mount()` accesses DB via `self.app.db` (line 119 in tui.py)
- **Does NOT override `ScrollableContainer` CSS** — per CLAUDE.md constraint
- Graceful fallback when session column is missing
- All Rich content in `Static` widgets uses `rich.text.Text` objects (not markup strings)

#### `StepDrillScreen`

```python
class StepDrillScreen(Screen):
    """Step reliability + repo health profiles."""

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("q", "app.quit", "Quit"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        with ScrollableContainer():
            yield Static(id="step-header")
            yield Static(id="step-table")
            yield Static(id="repo-header")
            yield Static(id="repo-table")
            yield Static(id="under-instrumented")
        yield Footer()

    def on_mount(self) -> None:
        db: HooksDB = self.app.db  # type: ignore[attr-defined]
        self.app.sub_title = "Steps"

        # Step reliability table (sorted by pain index)
        self.query_one("#step-header", Static).update(
            Text("\n  Step Reliability (last 7d, sorted by pain index)", style="bold")
        )

        steps = db.step_reliability(days=7)
        if steps:
            self.query_one("#step-table", Static).update(
                render.step_reliability_table(steps)
            )
        else:
            self.query_one("#step-table", Static).update(
                Text("  No step data found.", style="dim")
            )

        # Repo profiles
        self.query_one("#repo-header", Static).update(
            Text("\n  Repo Health Profiles (last 7d)", style="bold")
        )

        repos = db.repo_profiles(days=7)
        if repos:
            self.query_one("#repo-table", Static).update(
                render.repo_profile_grid(repos)
            )
        else:
            self.query_one("#repo-table", Static).update(
                Text("  No repo data found.", style="dim")
            )

        # Under-instrumented repos
        under = db.under_instrumented_repos(days=7)
        if under:
            uit = Text("\n  Under-instrumented repos", style="bold yellow")
            uit.append(f" (< {__import__('hooks_report').config.MIN_STEPS_FOR_COVERAGE} distinct steps):\n")
            for repo, step_count in under:
                uit.append(f"    {repo}: {step_count} steps\n", style="yellow")
            self.query_one("#under-instrumented", Static).update(uit)
```

Notes:
- Step reliability sorted by pain index (query returns `ORDER BY total_s DESC` — re-sort in render helper or accept overhead-based ordering)
- Repo profiles include session count (0 if Phase 1 not deployed)
- Under-instrumented repos section only appears if there are flagged repos

### 3b. Changes to `hooks_report/tui.py`

#### Add keybindings (line 185-189 in current `BINDINGS`)

```python
BINDINGS = [
    Binding("d", "push_screen('detail')", "Detail"),
    Binding("s", "push_screen('sessions')", "Sessions"),   # NEW
    Binding("t", "push_screen('steps')", "Steps"),         # NEW
    Binding("r", "refresh_data", "Refresh"),
    Binding("e", "export", "Export"),
    Binding("q", "quit", "Quit"),
]
```

#### Install new screens in `on_mount()` (after line 206)

```python
def on_mount(self) -> None:
    self.install_screen(DetailScreen(), "detail")
    from .screens import SessionsScreen, StepDrillScreen
    self.install_screen(SessionsScreen(), "sessions")      # NEW
    self.install_screen(StepDrillScreen(), "steps")         # NEW
    self._populate_dashboard()
```

Import is inline (lazy) to keep the existing import structure — `screens.py` imports from `render.py` and `db.py`, no circular dependency risk.

#### Fix subtitle bug — add `on_screen_resume` event handler

The known bug (documented in TODO.md): TUI subtitle stays "Detail" (or "Sessions", "Steps") after pressing Escape to pop back to the dashboard. Fix by adding a method to `HooksReportApp`:

```python
def on_screen_resume(self) -> None:
    """Restore dashboard subtitle when returning from a pushed screen."""
    summary = self.db.assess()
    overhead_min = round(summary.overhead_24h_ms / 60000, 1)
    self.sub_title = f"24h: {summary.rel_total} runs · {overhead_min}m overhead · {datetime.now():%H:%M}"
```

Add after `_populate_dashboard()` (around line 228). The `on_screen_resume` event fires when a screen is popped from the stack and the previous screen resumes. This restores the dashboard subtitle with fresh data.

Notes:
- `on_screen_resume` is a Textual lifecycle event — no explicit wiring needed
- Re-queries `assess()` to also refresh the subtitle counts (minor overhead, ~1 SQLite query)

### 3c. New render helpers in `hooks_report/render.py`

Add after the existing `action_items_panel()` function (line 193).

#### `pain_index_cell(index: float) -> Text`

Color-coded pain index for table cells.

```python
def pain_index_cell(index: float) -> Text:
    """Color-coded pain index: red > PAIN_INDEX_RED, yellow > PAIN_INDEX_YELLOW, green."""
    label = f"{index:.1f}"
    if index >= config.PAIN_INDEX_RED:
        return Text(label, style="red bold")
    elif index >= config.PAIN_INDEX_YELLOW:
        return Text(label, style="yellow")
    return Text(label, style="green")
```

Notes:
- Uses `PAIN_INDEX_RED` and `PAIN_INDEX_YELLOW` from config (Phase 2)
- Import `config` is already present in render.py (line 8)

#### `session_table(sessions: list) -> Table`

Rich table for `SessionSummary` list.

```python
def session_table(sessions: list) -> Table:
    """Rich table for session summaries."""
    from .db import SessionSummary  # type hint only

    table = Table(box=None, padding=(0, 1), show_header=True, header_style="bold")
    table.add_column("Session", width=12)
    table.add_column("Duration", width=10, justify="right")
    table.add_column("Hooks", width=6, justify="right")
    table.add_column("Fails", width=6, justify="right")
    table.add_column("Tools", width=6, justify="right")
    table.add_column("Overhead", width=10, justify="right")
    table.add_column("Steps", width=6, justify="right")

    for s in sessions:
        # Truncate session ID for display
        sid = s.session_id[:12] if len(s.session_id) > 12 else s.session_id

        # Duration formatting
        if s.duration_s >= 3600:
            dur = f"{s.duration_s / 3600:.1f}h"
        elif s.duration_s >= 60:
            dur = f"{s.duration_s / 60:.0f}m"
        else:
            dur = f"{s.duration_s}s"

        # Failure count colored
        fail_cell = Text(str(s.hook_failures))
        if s.hook_failures > 0:
            fail_cell = Text(str(s.hook_failures), style="red")

        table.add_row(
            sid, dur, str(s.hook_runs), fail_cell,
            str(s.tool_uses), fmt_dur(s.overhead_ms),
            str(s.distinct_steps),
        )

    return table
```

Notes:
- Session IDs truncated to 12 chars (UUIDs are 36 chars — too wide for table display)
- Duration formatted as hours/minutes/seconds depending on magnitude
- Failure count in red when > 0 (consistent with existing patterns in render.py)

#### `step_reliability_table(steps: list) -> Table`

Rich table with p50/p90/p99 and pain index.

```python
def step_reliability_table(steps: list) -> Table:
    """Rich table for step reliability with percentiles and pain index."""
    from .db import StepReliability  # type hint only

    table = Table(box=None, padding=(0, 1), show_header=True, header_style="bold")
    table.add_column("Step", width=22)
    table.add_column("Runs", width=6, justify="right")
    table.add_column("Fail%", width=6, justify="right")
    table.add_column("p50", width=8, justify="right")
    table.add_column("p90", width=8, justify="right")
    table.add_column("p99", width=8, justify="right")
    table.add_column("Pain", width=7, justify="right")

    # Sort by pain index descending for display
    sorted_steps = sorted(steps, key=lambda s: s.pain_index, reverse=True)

    for s in sorted_steps:
        # Fail rate
        if s.fail_rate is None:
            fail_cell = Text("--", style="dim")
        elif s.fail_rate == 0:
            fail_cell = Text("0%", style="green")
        else:
            fail_cell = Text(f"{s.fail_rate:.1f}%", style="red" if s.fail_rate > 10 else "yellow")

        table.add_row(
            s.step,
            str(s.total_runs),
            fail_cell,
            fmt_dur(s.p50_ms),
            fmt_dur(s.p90_ms),
            fmt_dur(s.p99_ms),
            pain_index_cell(s.pain_index),
        )

    return table
```

Notes:
- Sorts by pain index descending (highest pain at top) regardless of query ordering
- Reuses `fmt_dur()` (line 12) for all duration columns
- Reuses `pain_index_cell()` for the Pain column

#### `repo_profile_grid(repos: list) -> Table`

Rich table for `RepoProfile` list.

```python
def repo_profile_grid(repos: list) -> Table:
    """Rich table for repo health profiles."""
    from .db import RepoProfile  # type hint only

    table = Table(box=None, padding=(0, 1), show_header=True, header_style="bold")
    table.add_column("Repo", width=30)
    table.add_column("Runs", width=7, justify="right")
    table.add_column("Fail%", width=7, justify="right")
    table.add_column("Steps", width=6, justify="right")
    table.add_column("Overhead", width=10, justify="right")
    table.add_column("Sessions", width=9, justify="right")
    table.add_column("Guard", width=6, justify="right")

    for r in repos:
        # Fail rate cell
        if r.fail_rate is None:
            fail_cell = Text("--", style="dim")
        elif r.fail_rate == 0:
            fail_cell = Text("0%", style="green")
        else:
            fail_cell = Text(f"{r.fail_rate:.1f}%", style="red")

        # Session count (0 means Phase 1 not deployed)
        session_cell = Text(str(r.session_count)) if r.session_count > 0 else Text("--", style="dim")

        # Guardrail density
        guard_cell = Text(f"{r.guardrail_density:.1f}") if r.guardrail_density > 0 else Text("--", style="dim")

        table.add_row(
            r.repo,
            str(r.total_runs),
            fail_cell,
            str(r.distinct_steps),
            f"{r.overhead_min:.1f}m",
            session_cell,
            guard_cell,
        )

    return table
```

Notes:
- "Guard" column = guardrail_density (distinct steps per session) — shows "--" when no session data
- Sessions column shows "--" when Phase 1 not deployed
- Fail rate styling consistent with existing patterns (`_projects_rich_table` in tui.py, line 93)

### 3d. New static output sections in `hooks_report/static.py`

Add after `section_projects_compact()` (line 263) and before the `# -- Verbose legacy sections` comment.

#### `section_step_reliability(console, db) -> None`

Per-step reliability with pain index. Verbose-only.

```python
def section_step_reliability(console: Console, db: HooksDB) -> None:
    _sep(console)
    console.print(Text("  Step Reliability (last 7d)", style="bold"))
    console.print()

    steps = db.step_reliability(days=7)
    if not steps:
        console.print(Text("  No step data.", style="dim"))
        return

    table = Table(box=None, padding=(0, 1), show_header=True)
    table.add_column("Step", width=22)
    table.add_column("Runs", width=6, justify="right")
    table.add_column("Fail%", width=6, justify="right")
    table.add_column("p50", width=8, justify="right")
    table.add_column("p90", width=8, justify="right")
    table.add_column("p99", width=8, justify="right")
    table.add_column("Pain", width=7, justify="right")

    sorted_steps = sorted(steps, key=lambda s: s.pain_index, reverse=True)
    for s in sorted_steps:
        if s.fail_rate is None:
            fail_cell = Text("--", style="dim")
        elif s.fail_rate == 0:
            fail_cell = Text("0%", style="green")
        else:
            fail_cell = Text(f"{s.fail_rate:.1f}%", style="red" if s.fail_rate > 10 else "yellow")

        table.add_row(
            s.step, str(s.total_runs), fail_cell,
            render.fmt_dur(s.p50_ms), render.fmt_dur(s.p90_ms), render.fmt_dur(s.p99_ms),
            render.pain_index_cell(s.pain_index),
        )

    console.print(table)
    console.print()
```

#### `section_repo_dashboard(console, db) -> None`

Per-repo health dashboard. Verbose-only.

```python
def section_repo_dashboard(console: Console, db: HooksDB) -> None:
    _sep(console)
    console.print(Text("  Repo Dashboard (last 7d)", style="bold"))
    console.print()

    repos = db.repo_profiles(days=7)
    if not repos:
        console.print(Text("  No repo data.", style="dim"))
        return

    console.print(render.repo_profile_grid(repos))
    console.print()

    # Under-instrumented repos
    under = db.under_instrumented_repos(days=7)
    if under:
        console.print(Text(f"  Under-instrumented repos (< {config.MIN_STEPS_FOR_COVERAGE} steps):", style="yellow bold"))
        for repo, steps in under:
            console.print(Text(f"    {repo}: {steps} steps", style="yellow"))
        console.print()
```

#### `section_sessions_compact(console, db) -> None`

Top worst sessions. Verbose-only, Phase 1 gated.

```python
def section_sessions_compact(console: Console, db: HooksDB) -> None:
    if not db._has_session_column():
        return  # Silently skip if session column not available

    _sep(console)
    console.print(Text("  Worst Sessions (last 7d)", style="bold"))
    console.print()

    sessions = db.session_list(days=7, limit=5)
    if not sessions:
        console.print(Text("  No session data.", style="dim"))
        console.print()
        return

    console.print(render.session_table(sessions))
    console.print()
```

Notes:
- `section_sessions_compact` returns silently (no output) if session column is absent — avoids confusing "Phase 1 required" messages in static output
- All three sections use the `_sep(console)` + `console.print(Text(...))` pattern from existing sections

#### Wire sections into `render_static()`

In `render_static()` (line 13 of static.py), modify the verbose block (lines 52-68):

```python
    if verbose:
        section_perf_compact(console, db, summary)
        section_step_reliability(console, db)      # NEW
        section_repo_dashboard(console, db)         # NEW
        section_sessions_compact(console, db)       # NEW
        section_projects_compact(console, db)
```

The three new sections go between `section_perf_compact` and `section_projects_compact` (logical grouping: performance -> step reliability -> repo health -> sessions -> projects).

### 3e. CLI additions in `hooks_report/cli.py`

Add two new arguments after the `--db` argument (line 31):

```python
parser.add_argument(
    "--sessions",
    action="store_true",
    help="Show per-session analysis (static mode, requires Phase 1 session column)",
)
parser.add_argument(
    "--step",
    metavar="NAME",
    help="Drill down into a specific step (static mode)",
)
```

### 3f. Dispatch in `hooks_report/__main__.py`

Add two new branches before the existing `args.export` check (line 13):

```python
def main():
    args = parse_args()
    db_path = args.db or os.environ.get("CLAUDE_HOOKS_DB") or config.DEFAULT_DB_PATH
    db = HooksDB(db_path)

    if args.export:
        from .static import export_json
        export_json(db)
    elif args.step:                                         # NEW
        from .static import render_step_drilldown
        render_step_drilldown(db, args.step)
    elif args.sessions:                                     # NEW
        from .static import render_sessions
        render_sessions(db)
    elif args.static or not sys.stdout.isatty():
        from .static import render_static
        render_static(db, verbose=args.verbose)
    else:
        from .tui import HooksReportApp
        HooksReportApp(db).run()
```

#### Dedicated static renderers for new CLI args

Add to `static.py` (after `export_json`, line 559):

```python
def render_step_drilldown(db: HooksDB, step_name: str) -> None:
    """Static output for --step NAME: per-repo breakdown, daily trend, exit codes."""
    console = Console()
    data = db.step_drilldown(step_name)

    _hdr(console, f"Step Drilldown: {step_name}")
    console.print()

    # Per-repo breakdown
    console.print(Text("  By Repo:", style="bold"))
    for r in data["by_repo"]:
        line = Text(f"    {r['repo']:<30}  {r['runs']:>5} runs  ")
        if r["failures"] > 0:
            line.append(f"{r['failures']} fail  ", style="red")
        line.append(f"avg={render.fmt_dur(r['avg_ms'])}  max={render.fmt_dur(r['max_ms'])}")
        console.print(line)

    # Daily trend
    console.print()
    console.print(Text("  Daily Trend:", style="bold"))
    for d in data["daily"]:
        line = Text(f"    {d['day']}  {d['runs']:>4} runs  ")
        if d["failures"] > 0:
            line.append(f"{d['failures']} fail  ", style="red")
        line.append(f"avg={render.fmt_dur(d['avg_ms'])}")
        console.print(line)

    # Exit code distribution
    console.print()
    console.print(Text("  Exit Codes:", style="bold"))
    for e in data["exit_codes"]:
        style = "red" if e["code"] != 0 else "green"
        console.print(Text(f"    exit={e['code']:<5}  {e['count']} occurrences", style=style))

    console.print()


def render_sessions(db: HooksDB) -> None:
    """Static output for --sessions: worst sessions table."""
    console = Console()

    if not db._has_session_column():
        console.print(Text(
            "\n  Session column not found. Deploy Phase 1 migration first.\n",
            style="yellow",
        ))
        return

    _hdr(console, "Sessions (last 7d, by failures)")
    console.print()

    sessions = db.session_list(days=7, limit=20)
    if not sessions:
        console.print(Text("  No session data found.", style="dim"))
        console.print()
        return

    console.print(render.session_table(sessions))
    console.print()
```

## Files Changed

| File | Change | Notes |
|------|--------|-------|
| `hooks_report/screens.py` | **NEW** ~120 lines | `SessionsScreen`, `StepDrillScreen` — 2 Textual Screen subclasses |
| `hooks_report/tui.py` | +2 bindings, +2 `install_screen()`, +1 `on_screen_resume()` | Bindings `s`=Sessions, `t`=Steps; subtitle bug fix via `on_screen_resume` |
| `hooks_report/render.py` | +4 functions | `pain_index_cell()`, `session_table()`, `step_reliability_table()`, `repo_profile_grid()` |
| `hooks_report/static.py` | +3 section functions, +2 dedicated renderers, wire into `render_static()` | `section_step_reliability()`, `section_repo_dashboard()`, `section_sessions_compact()`, `render_step_drilldown()`, `render_sessions()` |
| `hooks_report/cli.py` | +2 arguments | `--sessions`, `--step NAME` |
| `hooks_report/__main__.py` | +2 dispatch branches | `args.step` and `args.sessions` before existing `args.export` |

## Verification

```bash
# TUI: verify new screens appear in footer and respond to keybindings
~/.claude/hooks/hooks-report.sh
# Press 't' -> Steps screen should show step reliability table
# Press Escape -> subtitle should reset to dashboard format (subtitle bug fix)
# Press 's' -> Sessions screen should show fallback if Phase 1 not deployed

# Static: verify verbose mode includes new sections
~/.claude/hooks/hooks-report.sh --verbose 2>&1 | grep -A 5 "Step Reliability"
~/.claude/hooks/hooks-report.sh --verbose 2>&1 | grep -A 5 "Repo Dashboard"

# CLI: test --step drilldown
~/.claude/hooks/hooks-report.sh --step audit-logger
~/.claude/hooks/hooks-report.sh --step mermaid-lint

# CLI: test --sessions (should show fallback before Phase 1)
~/.claude/hooks/hooks-report.sh --sessions

# Verify imports don't break
python3 -c "from hooks_report.screens import SessionsScreen, StepDrillScreen; print('OK')"

# Verify render helpers
python3 -c "
from hooks_report.render import pain_index_cell
print(pain_index_cell(0.5))   # green
print(pain_index_cell(5.0))   # yellow
print(pain_index_cell(15.0))  # red
"

# Verify subtitle bug fix: on_screen_resume exists on HooksReportApp
python3 -c "
from hooks_report.tui import HooksReportApp
assert hasattr(HooksReportApp, 'on_screen_resume'), 'Missing on_screen_resume'
print('Subtitle fix: OK')
"
```

## Risks & Notes

- **`ScrollableContainer` CSS constraint**: Both new screens use `ScrollableContainer` without any CSS overrides. Per CLAUDE.md: "Do NOT override `ScrollableContainer` CSS" — its DEFAULT_CSS already handles `height: 1fr; overflow: auto auto`. Overriding collapses content to 1 row.
- **Rich Text objects only**: All `Static.update()` calls use `rich.text.Text` or `rich.table.Table` objects, never markup strings. Markup strings render as literal text in Textual `Static` widgets.
- **`on_screen_resume` event**: This is a Textual 0.x/8.x lifecycle event that fires when a screen is restored after a `pop_screen()`. It fires on the App, not on the Screen. If the Textual version changes this behavior, the subtitle fix will need updating.
- **Lazy imports in `tui.py`**: The `from .screens import ...` inside `on_mount()` avoids circular imports and keeps the import lightweight. `screens.py` imports from `render.py` and `db.py` (no `tui.py` imports).
- **`--step` SQL injection**: The step name is passed directly into SQL. This is safe for CLI usage (user controls their own machine). If ever exposed via a web interface, use parameterized queries.
- **Session column gating in static mode**: `section_sessions_compact()` returns silently (no output at all) when the session column is absent. `render_sessions()` (the `--sessions` CLI handler) prints an explicit warning. This difference is intentional: verbose mode shouldn't clutter output with "not available" for every missing feature, but explicit CLI flags should explain why they don't work.
- **`render_step_drilldown` and `render_sessions` dispatch order**: Both new CLI branches are checked before `args.static` in `__main__.py`. This means `--step NAME` and `--sessions` work without needing `--static` — they are always static-mode output. They should be checked after `--export` (which is the JSON output path) to avoid conflicts.
- **Pain index sort**: `step_reliability_table()` in render.py re-sorts by pain index descending, even though the query sorts by `total_s DESC`. This is intentional — pain index incorporates failure rate, making it a better ranking metric than raw overhead.
