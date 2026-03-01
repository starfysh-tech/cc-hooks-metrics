from __future__ import annotations

import json
from datetime import datetime
from rich.console import Console
from rich.text import Text
from rich.table import Table

from . import config, render
from .db import HooksDB, ReliabilitySummary


def render_static(db: HooksDB, verbose: bool = False) -> None:
    console = Console()

    # ── Traffic light summary + action items ──────────────────────────────────
    summary = db.assess()
    action_items = db.action_items()

    # Header
    overhead_min = round(summary.overhead_24h_ms / 60000)
    console.print()
    console.print(Text("══════════════════════════════════════════════════════════════", style="bold cyan"))
    console.print(Text("  Hooks Status", style="bold cyan"))
    console.print(Text("══════════════════════════════════════════════════════════════", style="bold cyan"))
    console.print(Text(f"  24h: {summary.rel_total} runs | {overhead_min} min overhead · {datetime.now():%Y-%m-%d %H:%M}", style="cyan"))
    console.print()
    console.print(render.traffic_light_grid(summary))

    # Action items
    any_issues = (
        summary.rel_failures > 0
        or summary.worst_pct >= config.TIMEOUT_YELLOW_PCT
        or summary.broken_count > 0
        or summary.regr_count > 0
    )
    if any_issues:
        console.print()
        console.print(Text("──────────────────────────────────────────────────────────────", style="cyan"))
        console.print(Text("  Action Items", style="bold"))
        console.print(Text("──────────────────────────────────────────────────────────────", style="cyan"))
        console.print()
        for item in render.action_items_panel(summary, action_items):
            console.print(item)
    else:
        console.print()
        console.print(Text("  All clear — no action items.", style="green"))

    # Trends section (REGR/SLOW in default; full WoW + FIXED/GONE in verbose)
    section_wow_compact(console, db, verbose=verbose)

    if verbose:
        section_perf_compact(console, db, summary)
        section_projects_compact(console, db)

    # Closing border
    console.print()
    console.print(Text("══════════════════════════════════════════════════════════════", style="bold cyan"))
    console.print()

    if verbose:
        section_health(console, db)
        section_failures(console, db)
        section_performance(console, db)
        section_usage(console, db)
        section_quality(console, db)
        section_projects(console, db)
        section_trends(console, db)


def _sep(console: Console):
    console.print(Text("──────────────────────────────────────────────────────────────", style="cyan"))


def _hdr(console: Console, title: str):
    console.print()
    console.print(Text("══════════════════════════════════════════════════════════════", style="bold cyan"))
    console.print(Text(f"  {title}", style="bold cyan"))
    console.print(Text("══════════════════════════════════════════════════════════════", style="bold cyan"))


# ── Compact sections ──────────────────────────────────────────────────────────


def section_perf_compact(console: Console, db: HooksDB, summary: ReliabilitySummary) -> None:
    _sep(console)
    runs_7d = summary.runs_7d
    overhead_7d_min = round(summary.overhead_7d_ms / 60000)

    console.print(Text(f"  Performance (last 7d — {runs_7d} runs, {overhead_7d_min} min total overhead)", style="bold"))
    console.print()

    table = Table(box=None, padding=(0, 1), show_header=True)
    table.add_column("Step", width=24)
    table.add_column("runs", width=6, justify="right")
    table.add_column("avg", width=7, justify="right")
    table.add_column("max", width=7, justify="right")
    table.add_column("timeout")

    rows = db.perf_compact()
    for row in rows:
        avg_fmt = render.fmt_dur(row.avg_ms)
        max_fmt = render.fmt_dur(row.max_ms)

        timeout = config.STEP_TIMEOUTS.get(row.step, 0)
        if timeout > 0:
            pct = round(row.max_ms / timeout * 100)
            bar_color = "red" if pct >= 100 else "yellow" if pct >= 80 else "cyan"
            bar = render.bar_chart(row.max_ms, timeout, 15, bar_color)
            timeout_text = Text()
            timeout_text.append_text(bar)
            timeout_text.append(f" {pct}%")
        elif row.max_ms > 30000:
            timeout_text = Text("⚠ no limit (max >30s)", style="yellow")
        else:
            timeout_text = Text("(no limit)", style="dim")

        table.add_row(row.step, str(row.total_n), avg_fmt, max_fmt, timeout_text)

    console.print(table)
    console.print()


def section_wow_compact(console: Console, db: HooksDB, verbose: bool = False) -> None:
    regressions = db.failure_regressions()
    improvements = db.failure_improvements()
    lat_regs = db.latency_regressions()
    gaps = db.coverage_gaps()

    # In default mode, skip entirely if there is nothing actionable to show
    if not verbose:
        has_content = bool(regressions or lat_regs or any(g.cur_r > 0 for g in gaps))
        if not has_content:
            return

    _sep(console)

    if verbose:
        console.print(Text("  Week-over-Week (last 7d vs prior 7d)", style="bold"))
        console.print()

        wow = db.wow_summary()

        # 4-row summary table
        table = Table(box=None, padding=(0, 1), show_header=False)
        table.add_column("metric", width=12)
        table.add_column("prev", width=8, justify="right")
        table.add_column("arrow", width=3)
        table.add_column("cur", width=8)
        table.add_column("delta")

        # Runs
        rdelta = wow.cur_runs - wow.prev_runs
        run_delta_text = Text()
        rsign = "+" if rdelta >= 0 else ""
        run_delta_text.append(f"{rsign}{rdelta} (")
        run_delta_text.append_text(render.pct_change(wow.cur_runs, wow.prev_runs, "neutral"))
        run_delta_text.append(")")
        table.add_row("Runs", str(wow.prev_runs), "→", str(wow.cur_runs), run_delta_text)

        # Failures
        fdelta = wow.cur_fail - wow.prev_fail
        fail_delta_text = Text()
        fsign = "+" if fdelta >= 0 else ""
        fail_delta_text.append(f"{fsign}{fdelta} (")
        fail_delta_text.append_text(render.pct_change(wow.cur_fail, wow.prev_fail, "lower_better"))
        fail_delta_text.append(")")
        table.add_row("Failures", str(wow.prev_fail), "→", str(wow.cur_fail), fail_delta_text)

        # Fail rate
        cur_rate_str = f"{wow.cur_rate:.1f}%" if wow.cur_rate is not None else "0.0%"
        prev_rate_str = f"{wow.prev_rate:.1f}%" if wow.prev_rate is not None else "0.0%"
        rdiff = (wow.cur_rate or 0) - (wow.prev_rate or 0)
        table.add_row("Fail rate", prev_rate_str, "→", cur_rate_str, Text(f"{rdiff:+.1f}pp"))

        # Overhead
        cur_min = f"{wow.cur_ms / 60000:.1f}"
        prev_min = f"{wow.prev_ms / 60000:.1f}"
        mdelta = (wow.cur_ms - wow.prev_ms) / 60000
        oh_delta_text = Text()
        oh_delta_text.append(f"{mdelta:+.1f} min (")
        oh_delta_text.append_text(render.pct_change(wow.cur_ms, wow.prev_ms, "neutral"))
        oh_delta_text.append(")")
        table.add_row("Overhead", f"{prev_min} m", "→", f"{cur_min} m", oh_delta_text)

        console.print(table)
        console.print()

    # Failure trends: REGR always shown, FIXED only in verbose
    shown_improvements = improvements if verbose else []
    if not regressions and not shown_improvements:
        if verbose:
            console.print(Text("  No failure trend changes.", style="green"))
    else:
        all_f = (
            [r.cur_f for r in regressions] + [r.prev_f for r in regressions]
            + [r.cur_f for r in shown_improvements] + [r.prev_f for r in shown_improvements]
        )
        max_fail = max(all_f) if all_f else 1

        for r in regressions:
            line = Text()
            line.append_text(render.trend_badge("REGR"))
            line.append(f"  {r.step:<22}  ")
            line.append_text(render.bar_chart(r.cur_f, max_fail, 14, "red"))
            line.append(f"  {r.cur_f:4d} fail  (was {r.prev_f}, ")
            line.append_text(render.pct_change(r.cur_f, r.prev_f, "lower_better"))
            line.append(")")
            console.print(line)

        for r in shown_improvements:
            line = Text()
            line.append_text(render.trend_badge("FIXED"))
            line.append(f"  {r.step:<22}  ")
            line.append_text(render.bar_chart(r.cur_f, max_fail, 14, "green"))
            line.append(f"  {r.cur_f:4d} fail  (was {r.prev_f}, ")
            line.append_text(render.pct_change(r.cur_f, r.prev_f, "lower_better"))
            line.append(")")
            console.print(line)

    # Latency regressions (SLOW — always shown)
    console.print()
    for r in lat_regs:
        line = Text()
        line.append_text(render.trend_badge("SLOW"))
        line.append(f"  {r.step:<22}  {render.fmt_dur(r.prev_avg)} → {render.fmt_dur(r.cur_avg)} avg  (")
        line.append_text(render.pct_change(r.cur_avg, r.prev_avg, "lower_better"))
        line.append(")")
        console.print(line)

    # Coverage gaps: NEW always shown, GONE only in verbose
    for g in gaps:
        if g.cur_r == 0:
            if verbose:
                line = Text()
                line.append_text(render.trend_badge("GONE"))
                line.append(f"  {g.step:<22}  — stopped (was {g.prev_r} runs)")
                console.print(line)
        else:
            line = Text()
            line.append_text(render.trend_badge("NEW"))
            line.append(f"  {g.step:<22}  — new ({g.cur_r} runs)")
            console.print(line)

    console.print()


def section_projects_compact(console: Console, db: HooksDB) -> None:
    _sep(console)
    console.print(Text("  Top Projects (last 7d)", style="bold"))
    console.print()

    projects = db.projects_compact()
    for p in projects:
        fail_i = int(p.fail_rate or 0)
        if fail_i > 0:
            line = Text()
            line.append(f"  {p.project:<32}  {p.total_min:>7.1f} min  {p.runs:>6} runs  ")
            line.append(f"{p.fail_rate:.1f}% fail rate", style="red")
            console.print(line)
        else:
            console.print(Text(f"  {p.project:<32}  {p.total_min:>7.1f} min  {p.runs:>6} runs"))
    console.print()


# ── Verbose legacy sections ──────────────────────────────────────────────────


def section_health(console: Console, db: HooksDB) -> None:
    _hdr(console, "a) Health Check (last 24h)")
    console.print()
    h = db.health_24h()
    console.print(f"  Total runs:       {h.total}")
    if h.failures > 0:
        console.print(Text(f"  Failures:         {h.failures} ({h.fail_pct}%)", style="red"))
    else:
        console.print(Text("  Failures:         0", style="green"))
    if h.review_runs > 0:
        console.print(f"  Review findings:  {h.review_findings} / {h.review_runs} runs (exit=1 = blocked)")
    console.print(f"  Total overhead:   {h.overhead_ms} ms")
    if h.max_latency_ms > 5000:
        console.print(Text(f"  Max latency:      {h.max_latency_ms} ms", style="red"))
    else:
        console.print(f"  Max latency:      {h.max_latency_ms} ms")
    if h.slow_count > 0:
        console.print(Text(f"  Runs >5s:         {h.slow_count}", style="yellow"))
    else:
        console.print(Text("  Runs >5s:         0", style="green"))


def section_failures(console: Console, db: HooksDB) -> None:
    _hdr(console, "b) Failure Report")
    console.print()
    console.print(Text("Per-step failure rates:", style="bold"))
    for step, count in db.failures_by_step():
        console.print(Text(f"  {step:<30} {count} failures", style="red"))

    _sep(console)
    console.print(Text("Exit code breakdown per step:", style="bold"))
    for step, code, count in db.exit_codes_by_step():
        console.print(f"  {step:<30} exit={code:<5} {count}")

    _sep(console)
    console.print(Text("Review hooks (exit=1 = findings blocked commit):", style="bold"))
    for step, runs, findings, pct in db.review_hook_stats():
        console.print(f"  {step:<30} {runs} runs  {findings} with findings ({pct}%)")

    _sep(console)
    console.print(Text("Exit-127 root causes (command not found):", style="bold"))
    for cmd, count in db.exit127_cmds():
        console.print(Text(f"  {cmd:<55} {count} occurrences", style="red"))

    _sep(console)
    console.print(Text("Timeout proximity — audit-logger (>1500ms = >75% of 2s timeout):", style="bold"))
    for step, dur, ts in db.near_timeout_rows():
        console.print(Text(f"  {step:<30} {dur:>6} ms  ({ts})", style="yellow"))


def section_performance(console: Console, db: HooksDB) -> None:
    _hdr(console, "c) Performance Report")
    console.print()
    console.print(Text("Per-step avg / p95 / max duration (ms):", style="bold"))
    for step, avg, p95, maxd, total in db.perf_full():
        p95_str = str(p95) if p95 is not None else "?"
        console.print(f"  {step:<30}  avg={avg:>7}  p95={p95_str:>7}  max={maxd:>7}  n={total}")

    _sep(console)
    console.print(Text("Timeout proximity (max duration as % of configured timeout):", style="bold"))
    for step, maxd in db.max_duration_by_step():
        limit = config.STEP_TIMEOUTS.get(step, 0)
        if limit > 0:
            pct = round(maxd / limit * 100)
            style = "red" if pct >= 80 else ("yellow" if pct >= 50 else "green")
            console.print(Text(f"  {step:<30} max={maxd:>6} ms  limit={limit:>6} ms  {pct:>3}% used", style=style))
        else:
            console.print(f"  {step:<30} max={maxd:>6} ms  (no timeout configured)")


def section_usage(console: Console, db: HooksDB) -> None:
    _hdr(console, "d) Usage Patterns")
    console.print()
    console.print(Text("Tool distribution (audit_events, all time):", style="bold"))
    for tool, count in db.tool_distribution():
        console.print(f"  {tool:<25} {count}")

    _sep(console)
    console.print(Text("Sessions (last 7 days):", style="bold"))
    sessions, events, avg = db.session_stats_7d()
    console.print(f"  Sessions:           {sessions}")
    console.print(f"  Total events:       {events}")
    console.print(f"  Avg/session:        {avg}")

    _sep(console)
    console.print(Text("Most-edited files (top 10, Edit+Write):", style="bold"))
    for fpath, count in db.most_edited_files():
        console.print(f"  {fpath:<65} {count}")

    _sep(console)
    console.print(Text("Bash command categories — first word (top 15):", style="bold"))
    for cat, count in db.bash_cmd_categories():
        console.print(f"  {cat:<35} {count}")


def section_quality(console: Console, db: HooksDB) -> None:
    _hdr(console, "e) Data Quality")
    console.print()
    zero = db.zero_timing_count()
    zero_style = "yellow" if zero > 0 else "green"
    line = Text("  Zero-timing rows:  ")
    line.append(str(zero), style=zero_style)
    console.print(line)

    unknown = db.unknown_hook_count()
    unk_style = "yellow" if unknown > 0 else "green"
    line = Text("  Unknown hook rows: ")
    line.append(str(unknown), style=unk_style)
    console.print(line)

    _sep(console)
    console.print(Text("Duplicate detection (same step+exit_code+ts truncated to second):", style="bold"))
    for step, code, ts_sec, n in db.duplicate_rows():
        console.print(Text(f"  {step:<30} exit={code:<5} n={n:<3} {ts_sec}", style="yellow"))


def section_projects(console: Console, db: HooksDB) -> None:
    _hdr(console, "f) Per-Project Cost (last 7d)")
    console.print()
    console.print(Text("Overhead by repo (total ms / failures / runs):", style="bold"))
    for project, total_ms, total_min, runs, failures in db.projects_full():
        if failures > 0:
            line = Text(f"  {project:<35} {total_ms:>8} ms  {total_min:>5} min  {runs:>5} runs  ")
            line.append(f"{failures} failures", style="red")
            console.print(line)
        else:
            console.print(f"  {project:<35} {total_ms:>8} ms  {total_min:>5} min  {runs:>5} runs")

    _sep(console)
    console.print(Text("Top steps per repo (last 7d):", style="bold"))
    prev_proj = None
    for project, step, runs, total_ms in db.top_steps_per_project():
        if project != prev_proj:
            console.print(f"\n  {project}:")
            prev_proj = project
        console.print(f"    {step:<25} {runs:>6} runs  {total_ms} ms")


def section_trends(console: Console, db: HooksDB) -> None:
    _hdr(console, "g) Week-over-Week Trends (last 7d vs prior 7d)")
    console.print()

    wow = db.wow_summary()

    # Summary table
    console.print(Text("Summary:", style="bold"))
    table = Table(box=None, padding=(0, 2), show_header=True)
    table.add_column("Metric", width=14)
    table.add_column("Last 7d", width=7, justify="right")
    table.add_column("Prior 7d", width=7, justify="right")
    table.add_column("Delta")

    cur_min = f"{wow.cur_ms / 60000:.1f}"
    prev_min = f"{wow.prev_ms / 60000:.1f}"

    # Runs
    run_delta = Text()
    run_delta.append(f"{wow.cur_runs - wow.prev_runs:+d}  (")
    run_delta.append_text(render.pct_change(wow.cur_runs, wow.prev_runs, "neutral"))
    run_delta.append(")")
    table.add_row("Runs", str(wow.cur_runs), str(wow.prev_runs), run_delta)

    # Failures
    fail_style = "red" if wow.cur_fail > wow.prev_fail else "green"
    fail_delta = Text()
    fail_delta.append(f"{wow.cur_fail - wow.prev_fail:+d}  (", style=fail_style)
    fail_delta.append_text(render.pct_change(wow.cur_fail, wow.prev_fail, "lower_better"))
    fail_delta.append(")", style=fail_style)
    table.add_row(
        "Failures",
        Text(str(wow.cur_fail), style=fail_style),
        str(wow.prev_fail),
        fail_delta,
    )

    # Fail rate
    rdiff = (wow.cur_rate or 0) - (wow.prev_rate or 0)
    rate_style = "red" if rdiff > 0 else "green"
    table.add_row(
        "Fail rate",
        f"{wow.cur_rate or 0:.1f}%",
        f"{wow.prev_rate or 0:.1f}%",
        Text(f"{rdiff:+.1f}pp", style=rate_style),
    )

    # Overhead
    oh_delta = Text()
    oh_delta.append(f"{(wow.cur_ms - wow.prev_ms) / 60000:+.1f} min  (")
    oh_delta.append_text(render.pct_change(wow.cur_ms, wow.prev_ms, "neutral"))
    oh_delta.append(")")
    table.add_row("Overhead", f"{cur_min} m", f"{prev_min} m", oh_delta)

    console.print(table)

    # Failure trends (full detail with bars)
    _sep(console)
    console.print(Text("Failure Trends:", style="bold"))

    all_regs = db.failure_regressions_full()
    all_imps = db.failure_improvements_full()
    all_f = [r.cur_f for r in all_regs + all_imps] + [r.prev_f for r in all_regs + all_imps]
    max_fail = max(all_f) if all_f else 1

    for r in all_regs:
        delta = r.cur_f - r.prev_f
        console.print()
        line1 = Text()
        line1.append_text(render.trend_badge("REGR"))
        line1.append(f"  {r.step}")
        console.print(line1)
        prior_line = Text("    Prior  ")
        prior_line.append_text(render.bar_chart(r.prev_f, max_fail))
        prior_line.append(f"  {r.prev_f:4d} failures")
        console.print(prior_line)
        last_line = Text("    Last   ", style="red")
        last_line.append_text(render.bar_chart(r.cur_f, max_fail, color="red"))
        last_line.append(f"  {r.cur_f:4d} failures   ▲ +{delta} (")
        last_line.append_text(render.pct_change(r.cur_f, r.prev_f, "lower_better"))
        last_line.append(")")
        console.print(last_line)

    for r in all_imps:
        delta = r.prev_f - r.cur_f
        console.print()
        line1 = Text()
        line1.append_text(render.trend_badge("FIXED"))
        line1.append(f"  {r.step}")
        console.print(line1)
        prior_line = Text("    Prior  ")
        prior_line.append_text(render.bar_chart(r.prev_f, max_fail))
        prior_line.append(f"  {r.prev_f:4d} failures")
        console.print(prior_line)
        last_line = Text("    Last   ", style="green")
        last_line.append_text(render.bar_chart(r.cur_f, max_fail, color="green"))
        last_line.append(f"  {r.cur_f:4d} failures   ▼ -{delta} (")
        last_line.append_text(render.pct_change(r.cur_f, r.prev_f, "lower_better"))
        last_line.append(")")
        console.print(last_line)

    # Coverage gaps (full)
    _sep(console)
    console.print(Text("Coverage Gaps:", style="bold"))
    console.print()
    gaps = db.coverage_gaps()
    for g in gaps:
        if g.cur_r == 0:
            line = Text()
            line.append_text(render.trend_badge("GONE"))
            line.append(f"  {g.step:<30} was {g.prev_r} runs    ")
            line.append("⚠ stopped running", style="yellow")
        else:
            line = Text()
            line.append_text(render.trend_badge("NEW"))
            line.append(f"  {g.step:<30} now {g.cur_r} runs    ")
            line.append("★ new step", style="cyan")
        console.print(line)

    # Latency regressions (full detail with bars)
    _sep(console)
    console.print(Text("Latency Regressions (avg duration increased >15%):", style="bold"))
    console.print()

    lat_regs_full = db.latency_regressions_full()
    all_avgs = [r.cur_avg for r in lat_regs_full] + [r.prev_avg for r in lat_regs_full]
    max_lat = max(all_avgs) if all_avgs else 1

    for r in lat_regs_full:
        delta_ms = r.cur_avg - r.prev_avg
        pct_raw = round((r.cur_avg - r.prev_avg) / r.prev_avg * 100) if r.prev_avg else 0
        color = "red" if pct_raw >= 30 else "yellow"
        console.print()
        badge_line = Text()
        badge_line.append("[SLOW]", style=color)
        badge_line.append(f"  {r.step}")
        console.print(badge_line)
        prior_line = Text("    Prior  ")
        prior_line.append_text(render.bar_chart(r.prev_avg, max_lat))
        prior_line.append(f"  {r.prev_avg:>7} ms avg")
        console.print(prior_line)
        last_line = Text("    Last   ", style=color)
        last_line.append_text(render.bar_chart(r.cur_avg, max_lat, color=color))
        last_line.append(f"  {r.cur_avg:>7} ms avg   ▲ +{delta_ms}ms (")
        last_line.append_text(render.pct_change(r.cur_avg, r.prev_avg, "lower_better"))
        last_line.append(")")
        console.print(last_line)


def export_json(db: HooksDB) -> None:
    """Print OTel-aligned JSON to stdout."""
    data = db.export_data()
    print(json.dumps(data, indent=2))
