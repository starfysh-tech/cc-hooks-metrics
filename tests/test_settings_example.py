import json
import os

ALL_EVENTS = {
    "Setup", "SessionStart", "SessionEnd", "UserPromptSubmit",
    "PreToolUse", "PostToolUse", "PostToolUseFailure",
    "PermissionRequest", "Notification", "Stop",
    "PreCompact", "SubagentStart", "SubagentStop",
}


def test_settings_example_is_valid_json():
    with open("settings-example.json") as f:
        data = json.load(f)
    assert "hooks" in data


def test_all_13_events_present():
    with open("settings-example.json") as f:
        data = json.load(f)
    hooks = data["hooks"]
    # Filter out _-prefixed documentation keys
    event_keys = {k for k in hooks if not k.startswith("_")}
    assert ALL_EVENTS == event_keys, f"Missing: {ALL_EVENTS - event_keys}, Extra: {event_keys - ALL_EVENTS}"


def test_referenced_scripts_exist():
    """All script paths referenced in settings-example.json should exist in the repo."""
    expected_scripts = {
        "hook-metrics.sh",
        "audit-logger.sh",
        "mermaid-lint.sh",
    }
    for script in expected_scripts:
        assert os.path.exists(script), f"Referenced script missing: {script}"


def test_guardrails_example_is_valid_json():
    with open("settings-guardrails-example.json") as f:
        data = json.load(f)
    expected = {
        "PreToolUse_guard-security", "PostToolUse_guard-python-lint",
        "PostToolUse_guard-python-typecheck", "PermissionRequest_guard-auto-allow",
    }
    actual = {k for k in data if not k.startswith("_")}
    assert actual == expected
