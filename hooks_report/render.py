from __future__ import annotations

from rich.text import Text
from rich.table import Table
from typing import Optional

from . import config
from .db import ReliabilitySummary, ActionItem


def fmt_dur(ms: int | float) -> str:
    """Format duration: '1.5s' if >=1000ms, else '250ms'."""
    ms_int = int(ms)
    if ms_int >= 1000:
        return f"{ms_int / 1000:.1f}s"
    return f"{ms_int}ms"


def bar_chart(val: int | float, max_val: int | float, width: int = 30) -> Text:
    """Proportional bar as Rich Text."""
    if max_val <= 0:
        return Text("░" * width, style="dim")
    filled = min(width, round(val / max_val * width))
    empty = width - filled
    t = Text()
    t.append("█" * filled, style="cyan")
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
    """Colored percentage change. Returns Text('(new)') if prev == 0."""
    if prev == 0:
        return Text("(new)", style="cyan")
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
        to_icon, to_detail = "❌", f"{summary.n_over} hooks exceeding timeout limits"
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
        rg_icon, rg_detail = "⚠️", f"{summary.regr_count} latency regressions adding >{config.IMPACT_THRESHOLD_S}s/week"
    else:
        rg_icon, rg_detail = "❌", f"{summary.regr_count} latency regressions adding >{config.IMPACT_THRESHOLD_S}s/week"

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
        t.append(f"{detail:<20}")
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
    """Returns list of Rich Text renderables for action items, or 'All clear'."""
    statuses = [
        summary.rel_failures == 0,
        summary.worst_pct < config.TIMEOUT_YELLOW_PCT,
        summary.broken_count == 0,
        summary.regr_count == 0,
    ]
    if all(statuses) and not action_items:
        return [Text("  All clear — no action items.", style="green")]

    items: list[Text] = []
    for item in action_items:
        icon = "❌" if item.severity == "red" else "⚠️ "
        line1 = Text()
        line1.append(f"  {icon} ", style="bold")
        line1.append(f"{item.category:<10}", style="bold")
        line1.append(f" {item.detail}")
        line2 = Text(f"     → {item.fix}", style="dim")
        items.append(line1)
        items.append(line2)
        items.append(Text(""))
    return items
