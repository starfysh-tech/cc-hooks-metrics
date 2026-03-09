#!/usr/bin/env python3
"""PostToolUse guardrail: runs tsc --noEmit after Write/Edit on .ts/.tsx files."""
import json
import os
import subprocess
import sys

APPLICABLE_TOOLS = {"Write", "Edit"}
TS_EXTENSIONS = (".ts", ".tsx")


def find_tsconfig(start_path: str) -> str | None:
    """Walk up from start_path's directory to find the nearest tsconfig.json."""
    directory = os.path.dirname(os.path.abspath(start_path))
    while True:
        candidate = os.path.join(directory, "tsconfig.json")
        if os.path.isfile(candidate):
            return candidate
        parent = os.path.dirname(directory)
        if parent == directory:
            return None
        directory = parent


def main():
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            print("guard-ts-typecheck: empty stdin, no-op", file=sys.stderr)
            sys.exit(0)
        payload = json.loads(raw)
    except json.JSONDecodeError:
        print("guard-ts-typecheck: malformed JSON from Claude, skipping check", file=sys.stderr)
        sys.exit(0)

    tool_name = payload.get("tool_name", "")
    if tool_name not in APPLICABLE_TOOLS:
        sys.exit(0)

    file_path = (payload.get("tool_input") or {}).get("file_path", "")
    if not file_path.endswith(TS_EXTENSIONS):
        sys.exit(0)

    if not os.path.isfile(file_path):
        sys.exit(0)

    tsconfig = find_tsconfig(file_path)
    if tsconfig is None:
        print(f"guard-ts-typecheck: no tsconfig.json found above {file_path}, skipping", file=sys.stderr)
        sys.exit(0)

    project_dir = os.path.dirname(tsconfig)

    # Prefer local tsc from node_modules/.bin; fall back to PATH tsc
    tsc_local = os.path.join(project_dir, "node_modules", ".bin", "tsc")
    tsc_cmd = tsc_local if os.path.isfile(tsc_local) else "tsc"

    try:
        result = subprocess.run(
            [tsc_cmd, "--noEmit", "--incremental", "--project", tsconfig],
            capture_output=True, text=True, timeout=30, cwd=project_dir,
        )
    except FileNotFoundError:
        # tsc not installed — skip gracefully
        sys.exit(0)
    except subprocess.TimeoutExpired:
        print(f"guard-ts-typecheck: tsc timed out after 30s on {file_path}, check skipped", file=sys.stderr)
        sys.exit(0)
    except OSError as e:
        print(f"guard-ts-typecheck: OS error running tsc: {e}", file=sys.stderr)
        sys.exit(0)

    if result.returncode != 0 and result.stdout.strip():
        output = result.stdout
        if len(output) > 500:
            output = output[:500] + "\n... (truncated, run `tsc --noEmit` for full output)"
        print(
            f"ACTION REQUIRED: Use the Edit tool to fix these type errors in {file_path}:\n{output}",
            file=sys.stderr,
        )
        sys.exit(2)
    elif result.returncode != 0:
        stderr_info = f" (stderr: {result.stderr.strip()})" if result.stderr.strip() else ""
        print(f"guard-ts-typecheck: tsc exited {result.returncode} with no findings on stdout{stderr_info}, check skipped", file=sys.stderr)

    sys.exit(0)


if __name__ == "__main__":
    main()
