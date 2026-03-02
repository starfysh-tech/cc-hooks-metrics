from __future__ import annotations

from itertools import groupby
from rich.text import Text
from rich.table import Table
from typing import Optional

from . import config
from .db import ReliabilitySummary, ActionItem, SessionSummary, StepReliability, RepoProfile, GuardrailSummary


def fmt_dur(ms: int | float) -> str:
    """Format duration: '1.5s' if >=1000ms, else '250ms'."""
    ms_int = int(ms)
    if ms_int >= 1000:
        return f"{ms_int / 1000:.1f}s"
    return f"{ms_int}ms"


def bar_chart(val: int | float, max_val: int | float, width: int = 30, color: str | None = None) -> Text:
    """Proportional bar as Rich Text."""
    if max_val <= 0:
        return Text("░" * width, style="dim")
    filled = min(width, round(val / max_val * width))
    empty = width - filled
    t = Text()
    t.append("█" * filled, style=color or "cyan")
    t.append("░" * empty, style="dim")
    return t


def trend_badge(badge_type: str) -> Text:
    """REGR=red, FIXED=green, NEW=cyan, GONE=yellow, SLOW=red."""
    styles = {
        "REGR": ("red", "[REGR]"),
        "FIXED": ("green", "[FIXED]"),
        "NEW": ("cyan", "[NEW]"),
        "GONE": ("yellow", "[GONE]"),
        "SLOW": ("red", "[SLOW]"),
    }
    style, label = styles.get(badge_type, ("dim", f"[{badge_type}]"))
    return Text(label, style=style)


def pct_change(cur: float, prev: float, polarity: str = "neutral") -> Text:
    """Colored percentage change. Returns Text('new') if prev == 0."""
    if prev == 0:
        return Text("new", style="cyan")
    pct = (cur - prev) / prev * 100
    label = f"{pct:+.1f}%"
    style = ""
    if polarity == "lower_better":
        if pct > 10:
            style = "red"
        elif pct < -10:
            style = "green"
    elif polarity == "higher_better":
        if pct < -10:
            style = "red"
        elif pct > 10:
            style = "green"
    return Text(label, style=style)


def traffic_light_grid(summary: ReliabilitySummary) -> Table:
    """
    2-column grid, 3 rows:
      Row 1: Reliability | Performance
      Row 2: Broken Hooks | Regressions
      Row 3: Review Gate (solo)
    """
    # Reliability
    if summary.rel_failures == 0:
        rel_icon, rel_detail = "✅", ""
    elif summary.rel_failures < config.RELIABILITY_RED_FAILURES and (summary.rel_fail_rate or 0) < config.RELIABILITY_RED_RATE:
        rel_icon, rel_detail = "⚠️", f"{summary.rel_failures} failures ({summary.rel_fail_rate}%)"
    else:
        rel_icon, rel_detail = "❌", f"{summary.rel_failures} failures ({summary.rel_fail_rate}%)"

    # Performance (timeout)
    if summary.worst_pct >= config.TIMEOUT_RED_PCT:
        to_icon, to_detail = "❌", f"{summary.n_over} over timeout"
    elif summary.worst_pct >= config.TIMEOUT_YELLOW_PCT:
        to_icon, to_detail = "⚠️", "hooks approaching timeout limits"
    else:
        to_icon, to_detail = "✅", ""

    # Broken hooks
    if summary.broken_count == 0:
        br_icon, br_detail = "✅", ""
    elif summary.broken_count < config.BROKEN_RED_COUNT:
        br_icon, br_detail = "⚠️", f"{summary.broken_count} exit-127 runs ({summary.broken_steps} steps)"
    else:
        br_icon, br_detail = "❌", f"{summary.broken_count} exit-127 runs ({summary.broken_steps} steps)"

    # Regressions
    if summary.regr_count == 0:
        rg_icon, rg_detail = "✅", ""
    elif summary.regr_count <= 2:
        rg_icon, rg_detail = "⚠️", f"{summary.regr_count} regressions (>{config.IMPACT_THRESHOLD_S}s/wk)"
    else:
        rg_icon, rg_detail = "❌", f"{summary.regr_count} regressions (>{config.IMPACT_THRESHOLD_S}s/wk)"

    # Review gate
    if summary.review_runs > 0:
        rpct = round(summary.review_findings / summary.review_runs * 100) if summary.review_runs else 0
        rv_icon, rv_detail = "✅", f"{rpct}% finding rate ({summary.review_runs} runs)"
    else:
        rv_icon, rv_detail = "⚠️", "no data"

    icon_style_map = {"✅": "green", "⚠️": "yellow", "❌": "red"}

    def _cell(label: str, icon: str, detail: str) -> Text:
        t = Text()
        t.append(f"  {label:<16} ", style="bold")
        t.append(icon + "  ", style=icon_style_map.get(icon, ""))
        t.append(f"{detail}")
        return t

    grid = Table.grid(padding=(0, 2))
    grid.add_column()
    grid.add_column()
    grid.add_row(
        _cell("Reliability", rel_icon, rel_detail),
        _cell("Performance", to_icon, to_detail),
    )
    grid.add_row(
        _cell("Broken Hooks", br_icon, br_detail),
        _cell("Regressions", rg_icon, rg_detail),
    )
    grid.add_row(
        _cell("Review Gate", rv_icon, rv_detail),
        Text(""),
    )
    return grid


def action_items_panel(
    summary: ReliabilitySummary,
    action_items: list[ActionItem],
) -> list[Text]:
    """Returns list of Rich Text renderables for action items, grouped by step.

    Multiple issues for the same step are merged into one entry. BROKEN takes
    priority for the fix suggestion. Returns 'All clear' when nothing to do.
    """
    statuses = [
        summary.rel_failures == 0,
        summary.worst_pct < config.TIMEOUT_YELLOW_PCT,
        summary.broken_count == 0,
        summary.regr_count == 0,
    ]
    if all(statuses) and not action_items:
        return [Text("  All clear — no action items.", style="green")]

    # Sort by step (for grouping), then by priority within each step
    priority = {"BROKEN": 0, "TIMEOUT": 1, "SLOW": 2, "FAIL": 3}
    sorted_items = sorted(action_items, key=lambda x: (x.step, priority.get(x.category, 99)))

    # Collect groups with severity rank so we can sort red before yellow
    groups: list[tuple[int, list[Text]]] = []
    for step, group in groupby(sorted_items, key=lambda x: x.step):
        step_items = list(group)

        # Severity: red if any item is red
        severity = "red" if any(i.severity == "red" for i in step_items) else "yellow"
        severity_rank = 0 if severity == "red" else 1
        icon = "❌" if severity == "red" else "⚠️ "

        # Fix from highest-priority category (first after sort)
        best_fix = step_items[0].fix

        # Strip the step name prefix from each detail to avoid repetition
        stripped_details = []
        for item in step_items:
            d = item.detail
            if d.startswith(f"{step} — "):
                d = d[len(f"{step} — "):]
            elif d.startswith(f"{step} "):
                d = d[len(f"{step} "):]
            stripped_details.append(d)

        line1 = Text()
        line1.append(f"  {icon} ", style="bold")
        line1.append(f"{step} — {', '.join(stripped_details)}")
        line2 = Text(f"     → {best_fix}", style="dim")
        groups.append((severity_rank, [line1, line2]))

    groups.sort(key=lambda x: x[0])
    items: list[Text] = []
    for _, group_lines in groups:
        items.extend(group_lines)
    return items


def pain_index_cell(index: float) -> Text:
    """Color-coded pain index: red bold >= PAIN_INDEX_RED, yellow >= PAIN_INDEX_YELLOW, else green."""
    label = f"{index:.1f}"
    if index >= config.PAIN_INDEX_RED:
        return Text(label, style="red bold")
    elif index >= config.PAIN_INDEX_YELLOW:
        return Text(label, style="yellow")
    return Text(label, style="green")


def fail_rate_cell(rate: Optional[float]) -> Text:
    """Color-coded fail rate: dim '—' for None, red for > 0, green for 0."""
    if rate is None:
        return Text("—", style="dim")
    if rate > 0:
        return Text(f"{rate:.1f}%", style="red")
    return Text("0%", style="green")


def failures_cell(n: int) -> Text:
    """Red if n > 0, plain otherwise."""
    return Text(str(n), style="red") if n > 0 else Text(str(n))


def fmt_session_dur(seconds: int) -> str:
    """Format session duration as h/m/s string."""
    if seconds >= 3600:
        return f"{seconds // 3600}h{(seconds % 3600) // 60}m"
    if seconds >= 60:
        return f"{seconds // 60}m{seconds % 60}s"
    return f"{seconds}s"


def build_session_table(sessions: list[SessionSummary]) -> Table:
    """Shared session table builder used by TUI and static renderers."""
    table = Table(box=None, padding=(0, 1), show_header=True, header_style="bold")
    table.add_column("Session", width=14)
    table.add_column("Duration", width=10, justify="right")
    table.add_column("Hooks", width=6, justify="right")
    table.add_column("Fails", width=6, justify="right")
    table.add_column("Tools", width=6, justify="right")
    table.add_column("Overhead", width=10, justify="right")
    table.add_column("Steps", width=6, justify="right")

    for s in sessions:
        table.add_row(
            s.session_id[:12], fmt_session_dur(s.duration_s), str(s.hook_runs),
            failures_cell(s.hook_failures),
            str(s.tool_uses), fmt_dur(s.overhead_ms), str(s.distinct_steps),
        )
    return table


def build_repo_profile_table(repos: list[RepoProfile], under_set: set[str]) -> Table:
    """Shared repo profile table builder; marks under-instrumented repos with yellow ⚠."""
    table = Table(box=None, padding=(0, 1), show_header=True, header_style="bold")
    table.add_column("Repo", width=32)
    table.add_column("Runs", width=6, justify="right")
    table.add_column("Fail%", width=7, justify="right")
    table.add_column("Steps", width=6, justify="right")
    table.add_column("Overhead", width=10, justify="right")
    table.add_column("Sessions", width=9, justify="right")
    table.add_column("Guard", width=7, justify="right")

    for r in repos:
        if r.repo in under_set:
            repo_cell = Text(f"{r.repo} ")
            repo_cell.append("⚠", style="yellow")
        else:
            repo_cell = Text(r.repo)
        session_cell = Text("—", style="dim") if r.session_count == 0 else Text(str(r.session_count))
        guard_cell = Text("—", style="dim") if r.guardrail_density == 0 else Text(f"{r.guardrail_density:.2f}")
        table.add_row(
            repo_cell, str(r.total_runs), fail_rate_cell(r.fail_rate), str(r.distinct_steps),
            fmt_dur(r.overhead_ms), session_cell, guard_cell,
        )
    return table


def build_step_reliability_table(steps: list[StepReliability]) -> Table:
    """Shared step reliability table builder, sorted by pain_index desc."""
    table = Table(box=None, padding=(0, 1), show_header=True, header_style="bold")
    table.add_column("Step", width=24)
    table.add_column("Runs", width=6, justify="right")
    table.add_column("Fail%", width=7, justify="right")
    table.add_column("p50", width=7, justify="right")
    table.add_column("p90", width=7, justify="right")
    table.add_column("p99", width=7, justify="right")
    table.add_column("Pain", width=7, justify="right")

    for s in sorted(steps, key=lambda x: x.pain_index, reverse=True):
        table.add_row(
            s.step, str(s.total_runs), fail_rate_cell(s.fail_rate),
            fmt_dur(s.p50_ms), fmt_dur(s.p90_ms), fmt_dur(s.p99_ms),
            pain_index_cell(s.pain_index),
        )
    return table


def guardrail_traffic_light(block_rate: float) -> tuple[str, str]:
    """Green <5% block rate, yellow 5-20%, red >20%."""
    if block_rate > 20:
        return "❌", f"{block_rate:.1f}% block rate"
    elif block_rate >= 5:
        return "⚠️", f"{block_rate:.1f}% block rate"
    return "✅", ""


def build_guardrail_table(guardrails: list[GuardrailSummary]) -> Table:
    """Table for guardrail summary: step | runs | blocks | block% | avg."""
    table = Table(box=None, padding=(0, 1), show_header=True, header_style="bold")
    table.add_column("Guard", width=24)
    table.add_column("Runs", width=7, justify="right")
    table.add_column("Blocks", width=7, justify="right")
    table.add_column("Block%", width=8, justify="right")
    table.add_column("Avg", width=8, justify="right")

    for g in guardrails:
        block_pct = Text(f"{g.block_rate:.1f}%" if g.block_rate is not None else "—",
                         style="red" if (g.block_rate or 0) > 20 else "")
        table.add_row(g.step, str(g.total_runs), str(g.blocks), block_pct, fmt_dur(g.avg_ms))
    return table
