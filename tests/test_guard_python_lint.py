import json
import os
import subprocess
import sys
import tempfile

SCRIPT = "guardrails/guard-python-lint.py"


def _run(tool_name: str, file_path: str) -> subprocess.CompletedProcess:
    payload = {"tool_name": tool_name, "tool_input": {"file_path": file_path}}
    return subprocess.run(
        [sys.executable, SCRIPT],
        input=json.dumps(payload),
        capture_output=True, text=True,
    )


def test_clean_py_file():
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write("x = 1\n")
        f.flush()
        r = _run("Write", f.name)
    os.unlink(f.name)
    assert r.returncode == 0

def test_py_with_lint_errors():
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write("import os\nimport sys\nx = 1\n")  # unused imports
        f.flush()
        r = _run("Edit", f.name)
    os.unlink(f.name)
    # Only assert exit 2 if ruff is installed
    if r.returncode == 2:
        assert "ACTION REQUIRED" in r.stderr

def test_non_python_file_skip():
    r = _run("Write", "/tmp/test.js")
    assert r.returncode == 0

def test_non_existent_file():
    r = _run("Write", "/tmp/nonexistent_abc123.py")
    assert r.returncode == 0

def test_non_write_tool_skip():
    payload = {"tool_name": "Read", "tool_input": {"file_path": "test.py"}}
    r = subprocess.run(
        [sys.executable, SCRIPT],
        input=json.dumps(payload),
        capture_output=True, text=True,
    )
    assert r.returncode == 0

def test_malformed_json():
    r = subprocess.run(
        [sys.executable, SCRIPT],
        input="not json", capture_output=True, text=True,
    )
    assert r.returncode == 0
    assert "malformed JSON" in r.stderr


# --- Fake-ruff tests (C4: unconditional block path validation) ---

def test_lint_errors_block_with_fake_ruff(tmp_path):
    """Unconditionally test lint errors produce exit 2 (no ruff dependency)."""
    fake_ruff = tmp_path / "ruff"
    fake_ruff.write_text("#!/bin/sh\necho 'test.py:1:1: F401 imported but unused'\nexit 1\n")
    fake_ruff.chmod(0o755)
    bad_py = tmp_path / "bad.py"
    bad_py.write_text("import os\n")
    env = os.environ.copy()
    env["PATH"] = str(tmp_path) + ":" + env.get("PATH", "")
    r = subprocess.run(
        [sys.executable, SCRIPT],
        input=json.dumps({"tool_name": "Write", "tool_input": {"file_path": str(bad_py)}}),
        capture_output=True, text=True, env=env,
    )
    assert r.returncode == 2
    assert "ACTION REQUIRED" in r.stderr

def test_ruff_internal_error_logs_warning(tmp_path):
    """ruff exit != 0 with no stdout should log warning but not block."""
    fake_ruff = tmp_path / "ruff"
    fake_ruff.write_text("#!/bin/sh\necho 'internal error' >&2\nexit 2\n")
    fake_ruff.chmod(0o755)
    bad_py = tmp_path / "bad.py"
    bad_py.write_text("x = 1\n")
    env = os.environ.copy()
    env["PATH"] = str(tmp_path) + ":" + env.get("PATH", "")
    r = subprocess.run(
        [sys.executable, SCRIPT],
        input=json.dumps({"tool_name": "Write", "tool_input": {"file_path": str(bad_py)}}),
        capture_output=True, text=True, env=env,
    )
    assert r.returncode == 0
    assert "no findings on stdout" in r.stderr


def test_empty_stdin_exits_zero():
    r = subprocess.run(
        [sys.executable, SCRIPT],
        input="", capture_output=True, text=True,
    )
    assert r.returncode == 0
    assert "empty stdin" in r.stderr


# --- Null tool_input (T2) ---

def test_tool_input_null():
    payload = {"tool_name": "Write", "tool_input": None}
    r = subprocess.run(
        [sys.executable, SCRIPT],
        input=json.dumps(payload),
        capture_output=True, text=True,
    )
    assert r.returncode == 0
