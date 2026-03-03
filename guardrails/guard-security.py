#!/usr/bin/env python3
"""PreToolUse guardrail: blocks destructive Bash commands and .env file access.

Known limitation: regex-based detection can be bypassed (find -delete, variable
expansion). Goal is catching accidental destruction, not adversarial evasion.
"""
import json
import re
import sys

# Bash: destructive command patterns
BASH_BLOCKED = [
    r"rm\s+.*-[^\s]*[rf][^\s]*\s+(/|~|\$HOME|\*)",  # rm with force flags on dangerous targets
    r"sudo\s+rm\b",                                    # sudo rm anything
    r">\s*/etc/",                                       # redirect to system dirs
    r"chmod\s+777\s+/",                                 # chmod 777 /
    r"\bmkfs\.",                                         # format commands
    r"\bdd\b.*\bof=/dev/",                              # raw device writes
]

# .env: block access except .env.sample/.env.example/.env.template/.env.test
ENV_PATTERN = re.compile(r"\.env\b(?!\.(sample|example|template|test))")

# File tools that access paths
FILE_TOOL_PATH_FIELDS = {
    "Read": "file_path", "Write": "file_path", "Edit": "file_path",
    "MultiEdit": "file_path",
}


def _check_bash(command: str) -> str | None:
    """Check a single command segment against blocked patterns."""
    for pattern in BASH_BLOCKED:
        if re.search(pattern, command):
            return f"Blocked: matches pattern {pattern!r}"
    if ENV_PATTERN.search(command):
        return "Blocked: .env file access via Bash"
    return None


def _split_chained(command: str) -> list[str]:
    """Split on &&, ||, ;, | to check each segment."""
    return re.split(r"\s*(?:&&|\|\||;|\|)\s*", command)


def main():
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            sys.exit(0)
        payload = json.loads(raw)
    except json.JSONDecodeError:
        print("guard-security: malformed JSON from Claude, skipping check", file=sys.stderr)
        sys.exit(0)

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {})

    # Bash command checks
    if tool_name == "Bash":
        command = tool_input.get("command", "")
        for segment in _split_chained(command):
            reason = _check_bash(segment.strip())
            if reason:
                print(f"ACTION REQUIRED: {reason}. Rethink your approach.", file=sys.stderr)
                sys.exit(2)
        # Also check .env in the full command (catches cat .env in chained)
        if ENV_PATTERN.search(command):
            print("ACTION REQUIRED: .env file access blocked. Use .env.example instead.", file=sys.stderr)
            sys.exit(2)
        sys.exit(0)

    # File tool .env checks
    if tool_name in FILE_TOOL_PATH_FIELDS:
        field = FILE_TOOL_PATH_FIELDS[tool_name]
        path = tool_input.get(field, "")
        if ENV_PATTERN.search(path):
            print(f"ACTION REQUIRED: .env file access blocked via {tool_name}. Use .env.example instead.", file=sys.stderr)
            sys.exit(2)
        sys.exit(0)

    # All other tools: allow
    sys.exit(0)


if __name__ == "__main__":
    main()
