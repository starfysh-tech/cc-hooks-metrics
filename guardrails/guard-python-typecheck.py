#!/usr/bin/env python3
"""PostToolUse guardrail: runs ty check after Write/Edit on .py files."""
import json
import os
import subprocess
import sys

APPLICABLE_TOOLS = {"Write", "Edit"}


def main():
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            print("guard-python-typecheck: empty stdin, no-op", file=sys.stderr)
            sys.exit(0)
        payload = json.loads(raw)
    except json.JSONDecodeError:
        print("guard-python-typecheck: malformed JSON from Claude, skipping check", file=sys.stderr)
        sys.exit(0)

    tool_name = payload.get("tool_name", "")
    if tool_name not in APPLICABLE_TOOLS:
        sys.exit(0)

    file_path = (payload.get("tool_input") or {}).get("file_path", "")
    if not file_path.endswith(".py"):
        sys.exit(0)

    if not os.path.isfile(file_path):
        sys.exit(0)

    try:
        result = subprocess.run(
            ["ty", "check", file_path],
            capture_output=True, text=True, timeout=25,
        )
    except FileNotFoundError:
        print("guard-python-typecheck: ty not found — install with: brew install ty  OR  uv tool install ty  OR  cargo install ty", file=sys.stderr)
        sys.exit(0)
    except subprocess.TimeoutExpired:
        print(f"guard-python-typecheck: ty timed out after 25s on {file_path}, check skipped", file=sys.stderr)
        sys.exit(0)
    except OSError as e:
        print(f"guard-python-typecheck: OS error running ty: {e}", file=sys.stderr)
        sys.exit(0)

    if result.returncode != 0 and result.stdout.strip():
        output = result.stdout
        if len(output) > 500:
            output = output[:500] + "\n... (truncated, run `ty check` for full output)"
        print(
            f"ACTION REQUIRED: Use the Edit tool to fix these type errors in {file_path}:\n{output}",
            file=sys.stderr,
        )
        sys.exit(2)
    elif result.returncode != 0:
        stderr_info = f" (stderr: {result.stderr.strip()})" if result.stderr.strip() else ""
        print(f"guard-python-typecheck: ty exited {result.returncode} with no findings on stdout{stderr_info}, check skipped", file=sys.stderr)

    sys.exit(0)


if __name__ == "__main__":
    main()
