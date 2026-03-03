import json
import subprocess
import sys

SCRIPT = "guardrails/guard-security.py"


def _run(payload: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, SCRIPT],
        input=json.dumps(payload),
        capture_output=True, text=True,
    )


# --- Blocked commands ---

def test_blocks_rm_rf_root():
    r = _run({"tool_name": "Bash", "tool_input": {"command": "rm -rf /"}})
    assert r.returncode == 2
    assert "ACTION REQUIRED" in r.stderr

def test_blocks_rm_rf_home():
    r = _run({"tool_name": "Bash", "tool_input": {"command": "rm -rf ~"}})
    assert r.returncode == 2

def test_blocks_sudo_rm():
    r = _run({"tool_name": "Bash", "tool_input": {"command": "sudo rm foo"}})
    assert r.returncode == 2

def test_blocks_dd_dev():
    r = _run({"tool_name": "Bash", "tool_input": {"command": "dd if=/dev/zero of=/dev/sda"}})
    assert r.returncode == 2

def test_blocks_mkfs():
    r = _run({"tool_name": "Bash", "tool_input": {"command": "mkfs.ext4 /dev/sda1"}})
    assert r.returncode == 2

def test_blocks_chmod_777_root():
    r = _run({"tool_name": "Bash", "tool_input": {"command": "chmod 777 /"}})
    assert r.returncode == 2

def test_blocks_redirect_to_etc():
    r = _run({"tool_name": "Bash", "tool_input": {"command": "echo bad > /etc/passwd"}})
    assert r.returncode == 2


# --- Chaining detection ---

def test_blocks_chained_rm():
    r = _run({"tool_name": "Bash", "tool_input": {"command": "ls && rm -rf /"}})
    assert r.returncode == 2

def test_blocks_chained_env():
    r = _run({"tool_name": "Bash", "tool_input": {"command": "echo hi; cat .env"}})
    assert r.returncode == 2


# --- .env via file tools ---

def test_blocks_read_env():
    r = _run({"tool_name": "Read", "tool_input": {"file_path": "/app/.env"}})
    assert r.returncode == 2

def test_allows_read_env_example():
    r = _run({"tool_name": "Read", "tool_input": {"file_path": ".env.example"}})
    assert r.returncode == 0

def test_allows_read_env_sample():
    r = _run({"tool_name": "Read", "tool_input": {"file_path": ".env.sample"}})
    assert r.returncode == 0

def test_allows_read_env_template():
    r = _run({"tool_name": "Read", "tool_input": {"file_path": ".env.template"}})
    assert r.returncode == 0


# --- Allowed commands ---

def test_allows_ls():
    r = _run({"tool_name": "Bash", "tool_input": {"command": "ls -la"}})
    assert r.returncode == 0

def test_allows_git_status():
    r = _run({"tool_name": "Bash", "tool_input": {"command": "git status"}})
    assert r.returncode == 0

def test_allows_rm_single_file():
    r = _run({"tool_name": "Bash", "tool_input": {"command": "rm file.txt"}})
    assert r.returncode == 0


# --- Edge cases ---

def test_non_bash_tool_passthrough():
    r = _run({"tool_name": "Glob", "tool_input": {"pattern": "**/*.py"}})
    assert r.returncode == 0

def test_malformed_json():
    r = subprocess.run(
        [sys.executable, SCRIPT],
        input="not json", capture_output=True, text=True,
    )
    assert r.returncode == 2
    assert "BLOCKED" in r.stderr

def test_empty_stdin():
    r = subprocess.run(
        [sys.executable, SCRIPT],
        input="", capture_output=True, text=True,
    )
    assert r.returncode == 0
    assert "empty stdin" in r.stderr

def test_blocks_write_env():
    r = _run({"tool_name": "Write", "tool_input": {"file_path": "/app/.env"}})
    assert r.returncode == 2

def test_blocks_edit_env():
    r = _run({"tool_name": "Edit", "tool_input": {"file_path": ".env"}})
    assert r.returncode == 2

def test_blocks_read_env_local():
    r = _run({"tool_name": "Read", "tool_input": {"file_path": ".env.local"}})
    assert r.returncode == 2

def test_allows_env_test():
    r = _run({"tool_name": "Read", "tool_input": {"file_path": ".env.test"}})
    assert r.returncode == 0


# --- Additional blocked patterns (I8) ---

def test_blocks_rm_rf_home_var():
    r = _run({"tool_name": "Bash", "tool_input": {"command": "rm -rf $HOME"}})
    assert r.returncode == 2

def test_blocks_rm_rf_star():
    r = _run({"tool_name": "Bash", "tool_input": {"command": "rm -rf *"}})
    assert r.returncode == 2

def test_blocks_pipe_chained_destructive():
    r = _run({"tool_name": "Bash", "tool_input": {"command": "echo foo | rm -rf /"}})
    assert r.returncode == 2

def test_blocks_newline_chained():
    r = _run({"tool_name": "Bash", "tool_input": {"command": "ls\nrm -rf /"}})
    assert r.returncode == 2

def test_blocks_multiedit_env():
    r = _run({"tool_name": "MultiEdit", "tool_input": {"file_path": ".env"}})
    assert r.returncode == 2


def test_blocks_cat_env_unchained():
    r = _run({"tool_name": "Bash", "tool_input": {"command": "cat .env"}})
    assert r.returncode == 2
    assert "ACTION REQUIRED" in r.stderr


# --- Null tool_input (T2) ---

def test_tool_input_null():
    r = _run({"tool_name": "Bash", "tool_input": None})
    assert r.returncode == 0
