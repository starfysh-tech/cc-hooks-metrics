from __future__ import annotations

import os
import re

# ---------------------------------------------------------------------------
# Claude Code version requirements
# Keep in sync with version-requirements (sourced by bash scripts)
# ---------------------------------------------------------------------------
MIN_CC_VERSION = "2.1.50"          # Minimum: worktree hooks, core lifecycle events
RECOMMENDED_CC_VERSION = "2.1.85"  # Full feature set: conditional `if`, PermissionDenied, etc.


def parse_cc_version(version_string: str) -> tuple[int, ...] | None:
    """Parse 'Claude Code v2.1.85' or '2.1.85' → (2, 1, 85). Returns None on failure."""
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", version_string)
    if not m:
        return None
    return tuple(int(g) for g in m.groups())


STEP_TIMEOUTS: dict[str, int] = {  # milliseconds — max recorded + 20%, outlier-filtered
    # Claude Code hooks
    "audit-logger": 15_000,
    "codex-review": 360_000,
    "mermaid-lint": 45_000,
    "check-pr-labels": 65_000,
    "phi-check": 15_000,
    "lint-check": 85_000,
    "migration-check": 401_000,
    "stop-checks": 30_000,
    "type-check": 30_000,
    "block-destructive-gws": 5_000,
    "guard-security": 15_000,
    "guard-python-lint": 30_000,
    "guard-python-typecheck": 30_000,
    "guard-ts-typecheck": 37_000,
    "guard-auto-allow": 5_000,
    # Husky git hooks (imported via JSONL)
    "pytest": 1_000_000,
    "vitest": 120_000,
    "eslint": 30_000,
    "tsc": 30_000,
    "tslint": 20_000,
    "lint-staged": 36_000,
    "commitlint": 18_000,
    "prettier": 12_000,
    "dep-check": 3_300,
    "ruff-check": 1_900,
    "ruff-format": 2_300,
    "ruff-lint": 2_400,
    "wireframe-extract": 300,
    "no-verify-check": 230,
}
SEMANTIC_EXIT_STEPS = {"codex-review", "vitest", "pytest", "commitlint", "lint-staged"}
GUARDRAIL_STEPS = {"guard-security", "guard-python-lint", "guard-python-typecheck", "guard-ts-typecheck", "guard-auto-allow", "block-destructive-gws"}

# Steps expected to run regularly — used for coverage gap detection
# Derived from STEP_TIMEOUTS so there's only one list to maintain
EXPECTED_STEPS: set[str] = set(STEP_TIMEOUTS.keys())

EXIT_CODE_LABELS: dict[int, str] = {
    5: "I/O error",
    127: "binary not found",
    124: "timeout",
    141: "SIGPIPE",
    2: "guardrail block",
}
SKIP_HOOKS_PATTERN = re.compile(r"^(fake-fail|ok-step|echo|test-hook|main|event-log)$")

IMPACT_THRESHOLD_S = 30
REGRESSION_PCT_THRESHOLD = 0.15
FAILURE_REGRESSION_PCT = 0.10
MIN_RUNS_FOR_TREND = 5
SLOW_RUN_MS = 5000
RELIABILITY_RED_FAILURES = 10
RELIABILITY_RED_RATE = 5.0
BROKEN_RED_COUNT = 10
TIMEOUT_YELLOW_PCT = 80
TIMEOUT_RED_PCT = 100
PAIN_INDEX_RED = 10.0
PAIN_INDEX_YELLOW = 3.0
MIN_STEPS_FOR_COVERAGE = 3
DEFAULT_DB_PATH = os.path.expanduser("~/.claude/hooks.db")

SESSION_LIMIT_COMPACT = 5      # --verbose section: worst 5 by overhead
SESSION_LIMIT_STANDALONE = 20  # --sessions standalone renderer

# Advisor tuning thresholds
TUNING_HIGH_FAIL_RATE = 30.0       # % — "async" suggestion
TUNING_HIGH_FAIL_AVG_MS = 2000     # ms — combined with high fail rate
TUNING_NOISY_FAIL_RATE = 20.0      # % — "investigate" suggestion
TUNING_NOISY_MAX_AVG_MS = 500      # ms — cheap but noisy
TUNING_SLOW_MAX_FAIL_RATE = 5.0    # % — "optimize" suggestion
TUNING_SLOW_MIN_AVG_MS = 5000      # ms — low fail but slow
TUNING_MISSING_TIMEOUT_P99_MS = 10000  # ms — no timeout + p99 > this
HOT_SEQUENCE_FAIL_RATE = 20.0      # % — sequence failure threshold
SUMMARY_PERIODS = {"daily": 1, "weekly": 7}

# OTLP export (Phase 5)
OTLP_ENDPOINT_VAR = "HOOKS_METRICS_OTLP_ENDPOINT"
OTLP_HEADERS_VAR = "HOOKS_METRICS_OTLP_HEADERS"
OTLP_TIMEOUT_S = 10
OTLP_SERVICE_NAME = "claude-hooks"
OTLP_SERVICE_VERSION = "0.1.0"
OTLP_SCOPE_NAME = "hooks_report"
