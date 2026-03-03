import json
import os
import subprocess
import sys
import tempfile

SCRIPT = "guardrails/guard-python-typecheck.py"


def _run(tool_name: str, file_path: str) -> subprocess.CompletedProcess:
    payload = {"tool_name": tool_name, "tool_input": {"file_path": file_path}}
    return subprocess.run(
        [sys.executable, SCRIPT],
        input=json.dumps(payload),
        capture_output=True, text=True,
    )


def test_clean_py_file():
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write("x: int = 1\n")
        f.flush()
        r = _run("Write", f.name)
    os.unlink(f.name)
    assert r.returncode == 0

def test_non_python_skip():
    r = _run("Write", "/tmp/test.js")
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

def test_py_with_type_errors():
    """If ty is installed and finds errors, should exit 2 with ACTION REQUIRED."""
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write('x: int = "not an int"\n')
        f.flush()
        r = _run("Write", f.name)
    os.unlink(f.name)
    # Only assert exit 2 if ty is installed
    if r.returncode == 2:
        assert "ACTION REQUIRED" in r.stderr


# --- Fake-ty tests (C4: unconditional block path validation) ---

def test_type_errors_block_with_fake_ty(tmp_path):
    """Unconditionally test type errors produce exit 2 (no ty dependency)."""
    fake_ty = tmp_path / "ty"
    fake_ty.write_text("#!/bin/sh\necho 'error: invalid type assignment'\nexit 1\n")
    fake_ty.chmod(0o755)
    bad_py = tmp_path / "bad.py"
    bad_py.write_text('x: int = "not an int"\n')
    env = os.environ.copy()
    env["PATH"] = str(tmp_path) + ":" + env.get("PATH", "")
    r = subprocess.run(
        [sys.executable, SCRIPT],
        input=json.dumps({"tool_name": "Write", "tool_input": {"file_path": str(bad_py)}}),
        capture_output=True, text=True, env=env,
    )
    assert r.returncode == 2
    assert "ACTION REQUIRED" in r.stderr

def test_ty_internal_error_logs_warning(tmp_path):
    """ty exit != 0 with no stdout should log warning but not block."""
    fake_ty = tmp_path / "ty"
    fake_ty.write_text("#!/bin/sh\necho 'internal error' >&2\nexit 2\n")
    fake_ty.chmod(0o755)
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


def test_truncation_over_500_chars(tmp_path):
    """ty output >500 chars is truncated in the ACTION REQUIRED message."""
    fake_ty = tmp_path / "ty"
    fake_ty.write_text("#!/bin/sh\npython3 -c \"print('x'*600)\"\nexit 1\n")
    fake_ty.chmod(0o755)
    bad_py = tmp_path / "bad.py"
    bad_py.write_text('x: int = "not an int"\n')
    env = os.environ.copy()
    env["PATH"] = str(tmp_path) + ":" + env.get("PATH", "")
    r = subprocess.run(
        [sys.executable, SCRIPT],
        input=json.dumps({"tool_name": "Write", "tool_input": {"file_path": str(bad_py)}}),
        capture_output=True, text=True, env=env,
    )
    assert r.returncode == 2
    assert "... (truncated" in r.stderr


# --- Null tool_input (T2) ---

def test_tool_input_null():
    payload = {"tool_name": "Write", "tool_input": None}
    r = subprocess.run(
        [sys.executable, SCRIPT],
        input=json.dumps(payload),
        capture_output=True, text=True,
    )
    assert r.returncode == 0
