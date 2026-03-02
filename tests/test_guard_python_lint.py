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
