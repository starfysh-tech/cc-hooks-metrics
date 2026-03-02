import argparse
import os
from . import config


def parse_args():
    parser = argparse.ArgumentParser(
        prog="hooks-report",
        description="Claude Code hooks telemetry dashboard",
    )
    parser.add_argument(
        "--export",
        action="store_true",
        help="Output OTel-aligned JSON to stdout (no Rich/Textual)",
    )
    parser.add_argument(
        "--export-spans",
        action="store_true",
        help="Export claude.hooks.spans/v1 JSON to stdout",
    )
    parser.add_argument(
        "--include-sensitive",
        action="store_true",
        help="Include raw hostnames, full paths, and full tool inputs in span export (off by default)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Add 7 legacy detail sections (static mode only)",
    )
    parser.add_argument(
        "--static",
        action="store_true",
        help="Force Rich static output (no Textual TUI)",
    )
    parser.add_argument(
        "--db",
        metavar="PATH",
        default=None,
        help=f"Override DB path (default: {config.DEFAULT_DB_PATH}, env: CLAUDE_HOOKS_DB)",
    )
    parser.add_argument(
        "--sessions",
        action="store_true",
        help="Show per-session analysis (requires session column)",
    )
    parser.add_argument(
        "--step",
        metavar="NAME",
        help="Drill down into a specific step",
    )
    parser.add_argument(
        "--summary",
        choices=["daily", "weekly"],
        default=None,
        help="Generate periodic summary as JSON",
    )
    return parser.parse_args()
