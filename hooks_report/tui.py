from __future__ import annotations

from datetime import datetime
from rich.table import Table
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import ScrollableContainer
from textual.screen import Screen
from textual.widgets import Footer, Header, Static

from . import config, render
from .db import HooksDB


def _perf_rich_table(db: HooksDB) -> Table:
    """Build a Rich Table for the perf compact section."""
    table = Table(box=None, padding=(0, 1), show_header=True, header_style="bold")
    table.add_column("Step", width=24)
    table.add_column("Runs", width=6, justify="right")
    table.add_column("Avg", width=7, justify="right")
    table.add_column("Max", width=7, justify="right")
    table.add_column("Timeout")

    for row in db.perf_compact():
        avg_fmt = render.fmt_dur(row.avg_ms)
        max_fmt = render.fmt_dur(row.max_ms)
        timeout = config.STEP_TIMEOUTS.get(row.step, 0)
        if timeout > 0:
            pct = round(row.max_ms / timeout * 100)
            bar_color = "red" if pct >= 100 else "yellow" if pct >= 80 else "cyan"
            timeout_cell = Text()
            timeout_cell.append_text(render.bar_chart(row.max_ms, timeout, 12, bar_color))
            timeout_cell.append(f" {pct}%")
        elif row.max_ms > 30000:
            timeout_cell = Text("no limit", style="yellow")
        else:
            timeout_cell = Text("(no limit)", style="dim")
        table.add_row(row.step, str(row.total_n), avg_fmt, max_fmt, timeout_cell)

    return table


def _wow_rich_table(db: HooksDB) -> Table:
    """Build a Rich Table for the WoW summary."""
    wow = db.wow_summary()
    table = Table(box=None, padding=(0, 1), show_header=False)
    table.add_column("Metric", width=12)
    table.add_column("Prev", width=9, justify="right")
    table.add_column("", width=1)
    table.add_column("Cur", width=9)
    table.add_column("Delta")

    rdelta = wow.cur_runs - wow.prev_runs
    fdelta = wow.cur_fail - wow.prev_fail
    mdelta = (wow.cur_ms - wow.prev_ms) / 60000

    table.add_row(
        "Runs", str(wow.prev_runs), "→", str(wow.cur_runs),
        Text(f"{rdelta:+d}") + Text(" (") + render.pct_change(wow.cur_runs, wow.prev_runs, "neutral") + Text(")"),
    )
    table.add_row(
        "Failures", str(wow.prev_fail), "→", str(wow.cur_fail),
        Text(f"{fdelta:+d}") + Text(" (") + render.pct_change(wow.cur_fail, wow.prev_fail, "lower_better") + Text(")"),
    )
    table.add_row(
        "Fail rate",
        f"{wow.prev_rate or 0:.1f}%", "→", f"{wow.cur_rate or 0:.1f}%",
        Text(f"{(wow.cur_rate or 0) - (wow.prev_rate or 0):+.1f}pp"),
    )
    table.add_row(
        "Overhead",
        f"{wow.prev_ms / 60000:.1f} m", "→", f"{wow.cur_ms / 60000:.1f} m",
        Text(f"{mdelta:+.1f} min") + Text(" (") + render.pct_change(wow.cur_ms, wow.prev_ms, "neutral") + Text(")"),
    )
    return table


def _projects_rich_table(db: HooksDB) -> Table:
    """Build a Rich Table for top projects."""
    table = Table(box=None, padding=(0, 1), show_header=True, header_style="bold")
    table.add_column("Project", width=32)
    table.add_column("Total", width=9, justify="right")
    table.add_column("Runs", width=7, justify="right")
    table.add_column("Fail %", width=7, justify="right")

    for p in db.projects_compact():
        if p.fail_rate is None:
            fail_cell = Text("—", style="dim")
        elif p.fail_rate == 0:
            fail_cell = Text("0%", style="green")
        else:
            fail_cell = Text(f"{p.fail_rate:.1f}%", style="red")
        table.add_row(p.project, f"{p.total_min:.1f} min", str(p.runs), fail_cell)

    return table


class DetailScreen(Screen):
    """Detail view: WoW trends + latency regressions + projects."""

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("q", "app.quit", "Quit"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        with ScrollableContainer():
            yield Static(id="wow-header")
            yield Static(id="wow-table")
            yield Static(id="failure-trends")
            yield Static(id="latency-regressions")
            yield Static(id="projects-header")
            yield Static(id="projects-table")
        yield Footer()

    def on_mount(self) -> None:
        db: HooksDB = self.app.db  # type: ignore[attr-defined]
        self.app.sub_title = "Detail"

        self.query_one("#wow-header", Static).update(
            Text("\n  Week-over-Week (last 7d vs prior 7d)", style="bold")
        )
        self.query_one("#wow-table", Static).update(_wow_rich_table(db))

        # Failure trends
        regressions = db.failure_regressions()
        improvements = db.failure_improvements()
        ft = Text("\n  Failure Trends\n", style="bold")
        if not regressions and not improvements:
            ft.append("  No failure trend changes.", style="green")
        else:
            all_f = (
                [r.cur_f for r in regressions + improvements]
                + [r.prev_f for r in regressions + improvements]
            )
            max_fail = max(all_f) if all_f else 1
            for r in regressions:
                ft.append_text(render.trend_badge("REGR"))
                ft.append(f"  {r.step:<22}  ")
                ft.append_text(render.bar_chart(r.cur_f, max_fail, 14, "red"))
                ft.append(f"  {r.cur_f} fail  (was {r.prev_f}, ")
                ft.append_text(render.pct_change(r.cur_f, r.prev_f, "lower_better"))
                ft.append(")\n")
            for r in improvements:
                ft.append_text(render.trend_badge("FIXED"))
                ft.append(f"  {r.step:<22}  ")
                ft.append_text(render.bar_chart(r.cur_f, max_fail, 14, "green"))
                ft.append(f"  {r.cur_f} fail  (was {r.prev_f}, ")
                ft.append_text(render.pct_change(r.cur_f, r.prev_f, "lower_better"))
                ft.append(")\n")
        self.query_one("#failure-trends", Static).update(ft)

        # Latency regressions
        lat = Text("\n  Latency Regressions\n", style="bold")
        lat_regs = db.latency_regressions()
        if not lat_regs:
            lat.append("  No latency regressions.", style="green")
        else:
            for r in lat_regs:
                lat.append_text(render.trend_badge("SLOW"))
                lat.append(f"  {r.step:<22}  {render.fmt_dur(r.prev_avg)} → {render.fmt_dur(r.cur_avg)} avg  (")
                lat.append_text(render.pct_change(r.cur_avg, r.prev_avg, "lower_better"))
                lat.append(")\n")
        self.query_one("#latency-regressions", Static).update(lat)

        self.query_one("#projects-header", Static).update(
            Text("\n  Top Projects (last 7d)", style="bold")
        )
        self.query_one("#projects-table", Static).update(_projects_rich_table(db))


class HooksReportApp(App):
    """Textual TUI for hooks report — dashboard rendered directly on App."""

    CSS = """
    Static {
        width: 100%;
    }
    """

    TITLE = "Hooks Report"

    BINDINGS = [
        Binding("d", "push_screen('detail')", "Detail"),
        Binding("r", "refresh_data", "Refresh"),
        Binding("e", "export", "Export"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, db: HooksDB):
        super().__init__()
        self.db = db

    def compose(self) -> ComposeResult:
        yield Header()
        with ScrollableContainer():
            yield Static(id="traffic-lights")
            yield Static(id="action-items")
            yield Static(id="perf-header")
            yield Static(id="perf-table")
        yield Footer()

    def on_mount(self) -> None:
        self.install_screen(DetailScreen(), "detail")
        self._populate_dashboard()

    def _populate_dashboard(self) -> None:
        summary = self.db.assess()
        overhead_min = round(summary.overhead_24h_ms / 60000, 1)
        self.sub_title = f"24h: {summary.rel_total} runs · {overhead_min}m overhead · {datetime.now():%H:%M}"

        self.query_one("#traffic-lights", Static).update(render.traffic_light_grid(summary))

        action_items = self.db.action_items()
        panels = render.action_items_panel(summary, action_items)
        combined = Text()
        for p in panels:
            combined.append_text(p)
            combined.append("\n")
        self.query_one("#action-items", Static).update(combined)

        overhead_7d_min = round(summary.overhead_7d_ms / 60000)
        self.query_one("#perf-header", Static).update(
            Text(f"\n  Performance (last 7d — {summary.runs_7d} runs, {overhead_7d_min} min overhead)", style="bold")
        )
        self.query_one("#perf-table", Static).update(_perf_rich_table(self.db))

    def action_refresh_data(self) -> None:
        self._populate_dashboard()

    def action_export(self) -> None:
        import json
        from pathlib import Path
        data = self.db.export_data()
        out = Path("/tmp/hooks-export.json")
        out.write_text(json.dumps(data, indent=2))
        self.notify(f"Exported to {out}", title="Export")
