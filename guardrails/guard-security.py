#!/usr/bin/env python3
"""PreToolUse guardrail: blocks destructive Bash commands and .env file access.

Uses shlex-token predicates from `_patterns.py` rather than regex on raw
strings. Each rule is a small named function — adding a new dangerous flag
form is a new branch, not a regex tweak.

Limits: shlex does not see process substitution and treats backticks as
plain text. Goal is catching accidental destruction, not adversarial evasion.

Escape hatch: $GUARD_SECURITY_ALLOW (comma-separated rule names) skips
specific rules per-machine — e.g. `GUARD_SECURITY_ALLOW=blocks_force_push_main`.
"""
import json
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from _patterns import ENV_PATTERN, evaluate_command  # noqa: E402

FILE_TOOL_PATH_FIELDS = {
    "Read": "file_path",
    "Write": "file_path",
    "Edit": "file_path",
    "MultiEdit": "file_path",
}


def main() -> None:
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            print("guard-security: empty stdin, no-op", file=sys.stderr)
            sys.exit(0)
        payload = json.loads(raw)
    except json.JSONDecodeError:
        print("guard-security: BLOCKED — malformed JSON, cannot verify safety", file=sys.stderr)
        sys.exit(2)

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}

    if tool_name == "Bash":
        command = tool_input.get("command", "")
        blocked, reason = evaluate_command(command)
        if blocked:
            print(f"ACTION REQUIRED: Blocked: {reason}. Rethink your approach.", file=sys.stderr)
            sys.exit(2)
        if ENV_PATTERN.search(command):
            print("ACTION REQUIRED: Blocked: .env file access via Bash. Rethink your approach.", file=sys.stderr)
            sys.exit(2)
        sys.exit(0)

    if tool_name in FILE_TOOL_PATH_FIELDS:
        path = tool_input.get(FILE_TOOL_PATH_FIELDS[tool_name], "")
        if ENV_PATTERN.search(path):
            print(
                f"ACTION REQUIRED: .env file access blocked via {tool_name}. Use .env.example instead.",
                file=sys.stderr,
            )
            sys.exit(2)
        sys.exit(0)

    sys.exit(0)


if __name__ == "__main__":
    main()
