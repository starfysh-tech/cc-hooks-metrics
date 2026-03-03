import json
import subprocess
import sys

SCRIPT = "guardrails/guard-auto-allow.py"
ALLOW_JSON = {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": {"behavior": "allow"}}}


def _run(payload: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, SCRIPT],
        input=json.dumps(payload),
        capture_output=True, text=True,
    )


# --- Auto-allowed read-only tools ---

def test_auto_allows_read():
    r = _run({"tool_name": "Read", "tool_input": {"file_path": "foo.py"}})
    assert r.returncode == 0
    assert json.loads(r.stdout) == ALLOW_JSON

def test_auto_allows_glob():
    r = _run({"tool_name": "Glob", "tool_input": {"pattern": "*.py"}})
    assert r.returncode == 0
    assert json.loads(r.stdout) == ALLOW_JSON

def test_auto_allows_grep():
    r = _run({"tool_name": "Grep", "tool_input": {"pattern": "foo"}})
    assert r.returncode == 0
    assert json.loads(r.stdout) == ALLOW_JSON

def test_auto_allows_ls():
    r = _run({"tool_name": "LS", "tool_input": {}})
    assert r.returncode == 0
    assert json.loads(r.stdout) == ALLOW_JSON

def test_auto_allows_websearch():
    r = _run({"tool_name": "WebSearch", "tool_input": {"query": "test"}})
    assert r.returncode == 0
    assert json.loads(r.stdout) == ALLOW_JSON

def test_auto_allows_lsp():
    r = _run({"tool_name": "LSP", "tool_input": {}})
    assert r.returncode == 0
    assert json.loads(r.stdout) == ALLOW_JSON


# --- Safe Bash commands ---

def test_auto_allows_bash_ls():
    r = _run({"tool_name": "Bash", "tool_input": {"command": "ls -la"}})
    assert r.returncode == 0
    assert json.loads(r.stdout) == ALLOW_JSON

def test_auto_allows_bash_git_status():
    r = _run({"tool_name": "Bash", "tool_input": {"command": "git status"}})
    assert r.returncode == 0
    assert json.loads(r.stdout) == ALLOW_JSON

def test_auto_allows_bash_git_diff():
    r = _run({"tool_name": "Bash", "tool_input": {"command": "git diff HEAD~1"}})
    assert r.returncode == 0
    assert json.loads(r.stdout) == ALLOW_JSON


# --- Falls through (no output) ---

def test_unsafe_bash_falls_through():
    r = _run({"tool_name": "Bash", "tool_input": {"command": "rm -rf /tmp/foo"}})
    assert r.returncode == 0
    assert r.stdout.strip() == ""

def test_chained_bash_falls_through():
    r = _run({"tool_name": "Bash", "tool_input": {"command": "ls && rm foo"}})
    assert r.returncode == 0
    assert r.stdout.strip() == ""

def test_backtick_bash_falls_through():
    r = _run({"tool_name": "Bash", "tool_input": {"command": "echo `rm foo`"}})
    assert r.returncode == 0
    assert r.stdout.strip() == ""

def test_subshell_bash_falls_through():
    r = _run({"tool_name": "Bash", "tool_input": {"command": "echo $(rm foo)"}})
    assert r.returncode == 0
    assert r.stdout.strip() == ""

def test_write_tool_falls_through():
    r = _run({"tool_name": "Write", "tool_input": {"file_path": "foo.py"}})
    assert r.returncode == 0
    assert r.stdout.strip() == ""

def test_edit_tool_falls_through():
    r = _run({"tool_name": "Edit", "tool_input": {"file_path": "foo.py"}})
    assert r.returncode == 0
    assert r.stdout.strip() == ""


# --- Edge cases ---

def test_malformed_json():
    r = subprocess.run(
        [sys.executable, SCRIPT],
        input="not json", capture_output=True, text=True,
    )
    assert r.returncode == 0
    assert r.stdout.strip() == ""
    assert "malformed JSON" in r.stderr
