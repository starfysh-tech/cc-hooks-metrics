import os
import re

STEP_TIMEOUTS: dict[str, int] = {  # milliseconds
    "audit-logger": 2000,
    "mermaid-lint": 35000,
    "no-verify-gate": 5000,
    "check-pr-labels": 65000,
    "phi-check": 15000,
    "lint-check": 30000,
    "migration-check": 5000,
    "stop-checks": 30000,
}
SEMANTIC_EXIT_STEPS = {"codex-review"}
SKIP_HOOKS_PATTERN = re.compile(r"^(fake-fail|ok-step|echo|test-hook|main)$")
IMPACT_THRESHOLD_S = 30
REGRESSION_PCT_THRESHOLD = 0.15
FAILURE_REGRESSION_PCT = 0.10
MIN_RUNS_FOR_TREND = 5
SLOW_RUN_MS = 5000
RELIABILITY_RED_FAILURES = 10
RELIABILITY_RED_RATE = 5.0
BROKEN_RED_COUNT = 10
REGRESSION_RED_COUNT = 3
TIMEOUT_YELLOW_PCT = 80
TIMEOUT_RED_PCT = 100
DEFAULT_DB_PATH = os.path.expanduser("~/.claude/hooks.db")
