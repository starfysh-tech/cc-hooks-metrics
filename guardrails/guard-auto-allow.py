#!/usr/bin/env python3
"""PermissionRequest guardrail: auto-allows read-only operations."""
import json
import re
import sys

READ_ONLY_TOOLS = {"Read", "Glob", "Grep", "LS", "WebSearch", "LSP"}

SAFE_BASH_PATTERNS = [
    r"^ls\b",
    r"^pwd$",
    r"^echo\b(?!.*>)",
    r"^cat\b(?!.*>)",
    r"^head\b",
    r"^tail\b",
    r"^wc\b",
    r"^which\b",
    r"^type\b",
    r"^file\b",
    r"^stat\b",
    r"^git\s+(status|log|diff|show|branch|tag|remote\s+-v)\b",
    r"^npm\s+(list|ls|outdated|view)\b",
    r"^pip\s+(list|show|freeze)\b",
    r"^python\s+--version$",
    r"^node\s+--version$",
]

CHAINING_CHARS = re.compile(r"[;|&`]|\$\(")
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
            sys.exit(0)
        payload = json.loads(raw)
    except json.JSONDecodeError:
        print("guard-auto-allow: malformed JSON from Claude, skipping check", file=sys.stderr)
        sys.exit(0)

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {})

    # Unconditionally allow read-only tools
    if tool_name in READ_ONLY_TOOLS:
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
            if re.match(pattern, command):
                print(ALLOW_OUTPUT)
                sys.exit(0)
        # Not safelisted — fall through
        sys.exit(0)

    # All other tools: fall through to user prompt
    sys.exit(0)


if __name__ == "__main__":
    main()
