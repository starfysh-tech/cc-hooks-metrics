import os
import re

STEP_TIMEOUTS: dict[str, int] = {  # milliseconds
    "audit-logger": 5_000,
    "codex-review": 120_000,
    "mermaid-lint": 35000,
    "no-verify-gate": 5000,
    "check-pr-labels": 65000,
    "phi-check": 15000,
    "lint-check": 30000,
    "migration-check": 10_000,
    "stop-checks": 30000,
    "guard-security": 5000,
    "guard-python-lint": 30000,
    "guard-python-typecheck": 30000,
    "guard-auto-allow": 5000,
}
SEMANTIC_EXIT_STEPS = {"codex-review"}
GUARDRAIL_STEPS = {"guard-security", "guard-python-lint", "guard-python-typecheck", "guard-auto-allow"}
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
SESSION_LIMIT_TUI = 15         # TUI SessionsScreen
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
