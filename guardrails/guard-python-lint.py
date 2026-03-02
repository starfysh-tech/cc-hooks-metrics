#!/usr/bin/env python3
"""PostToolUse guardrail: runs ruff check after Write/Edit on .py files."""
import json
import subprocess
import sys

APPLICABLE_TOOLS = {"Write", "Edit"}


def main():
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            sys.exit(0)
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    tool_name = payload.get("tool_name", "")
    if tool_name not in APPLICABLE_TOOLS:
        sys.exit(0)

    import os
    file_path = payload.get("tool_input", {}).get("file_path", "")
    if not file_path.endswith(".py"):
        sys.exit(0)

    if not os.path.isfile(file_path):
        sys.exit(0)

    try:
        result = subprocess.run(
            ["ruff", "check", "--no-fix", file_path],
            capture_output=True, text=True, timeout=25,
        )
    except FileNotFoundError:
        # ruff not installed — skip gracefully
        sys.exit(0)
    except subprocess.TimeoutExpired:
        sys.exit(0)

    if result.returncode != 0 and result.stdout.strip():
        print(
            f"ACTION REQUIRED: Use the Edit tool to fix these ruff lint errors in {file_path}:\n{result.stdout}",
            file=sys.stderr,
        )
        sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    main()
