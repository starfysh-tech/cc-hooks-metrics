#!/usr/bin/env python3
"""SessionStart guardrail: scans the project's .claude/settings.json and .mcp.json
for known supply-chain hazards (OWASP ASI04 / CVE-2025-59536 / CVE-2026-21852).

Runs at session start. Inspects the git repo root (NOT cwd) so subdirectory
sessions still see the real project config. Uses JSON-structural walks rather
than substring matching so benign mentions of e.g. `apiUrl` in a description
don't trigger false positives.

Failure modes (explicit):
  missing file → exit 0 (no project config = nothing to audit)
  malformed JSON → exit 2 with line number + actionable stderr
  unreadable file → exit 2 (permission issue must be resolved, not silently skipped)
  scan finds hit → exit 2 with key path + reason

Pipe-to-shell detection in hook commands reuses the same predicate as
guard-security.py via guardrails/_patterns.py — single source of truth.
"""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from _patterns import blocks_pipe_to_shell, tokenize  # noqa: E402

# ── repo root resolution ─────────────────────────────────────────────────────


def _git_repo_root(start: Path) -> Path | None:
    """Walk up from `start` to find the enclosing git repo root, or None."""
    try:
        result = subprocess.run(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=2,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    root = result.stdout.strip()
    return Path(root) if root else None


# ── findings ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Finding:
    path: str
    key: str
    reason: str


# ── checks ───────────────────────────────────────────────────────────────────


def _check_pipe_to_shell(command: str) -> str:
    """Returns reason if command pipes a fetcher to a shell, else ''."""
    blocked, reason = blocks_pipe_to_shell(tokenize(command))
    return reason if blocked else ""


def _walk_hooks(node, key_path: str, findings: list[Finding], file_label: str) -> None:
    """Walk hook-style structures looking for command strings to inspect."""
    if isinstance(node, dict):
        cmd = node.get("command")
        if isinstance(cmd, str):
            reason = _check_pipe_to_shell(cmd)
            if reason:
                findings.append(Finding(file_label, f"{key_path}.command", reason))
        for k, v in node.items():
            _walk_hooks(v, f"{key_path}.{k}", findings, file_label)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _walk_hooks(v, f"{key_path}[{i}]", findings, file_label)


def _check_settings_json(path: Path, findings: list[Finding]) -> None:
    """Check a .claude/settings.json for ANTHROPIC_BASE_URL override and bad hooks."""
    label = str(path)
    data = _load_json_or_exit(path, label)
    if data is None or not isinstance(data, dict):
        return

    env = data.get("env") or {}
    if isinstance(env, dict) and "ANTHROPIC_BASE_URL" in env:
        findings.append(Finding(
            label, "env.ANTHROPIC_BASE_URL",
            f"overrides Anthropic API base URL to {env['ANTHROPIC_BASE_URL']!r}",
        ))

    if "apiUrl" in data:
        findings.append(Finding(label, "apiUrl", f"overrides API URL to {data['apiUrl']!r}"))

    hooks_block = data.get("hooks")
    if hooks_block is not None:
        _walk_hooks(hooks_block, "hooks", findings, label)


def _check_mcp_json(path: Path, findings: list[Finding]) -> None:
    """Check a .mcp.json for enableAllProjectMcpServers and per-server overrides."""
    label = str(path)
    data = _load_json_or_exit(path, label)
    if data is None or not isinstance(data, dict):
        return

    if data.get("enableAllProjectMcpServers") is True:
        findings.append(Finding(
            label, "enableAllProjectMcpServers",
            "auto-enables all project MCP servers without prompt",
        ))

    servers = data.get("mcpServers") or data.get("servers") or {}
    if isinstance(servers, dict):
        for name, cfg in servers.items():
            if not isinstance(cfg, dict):
                continue
            cmd = cfg.get("command")
            if isinstance(cmd, str):
                reason = _check_pipe_to_shell(cmd)
                if reason:
                    findings.append(Finding(
                        label, f"mcpServers.{name}.command", reason,
                    ))


def _load_json_or_exit(path: Path, label: str) -> Any:
    """Load JSON from `path`. Returns None if missing. exit(2) on parse/read errors."""
    try:
        text = path.read_text()
    except FileNotFoundError:
        return None
    except PermissionError as e:
        print(
            f"guard-incoming-config: BLOCKED — cannot read {label}: {e}. "
            "Fix file permissions before continuing.",
            file=sys.stderr,
        )
        sys.exit(2)
    except OSError as e:
        print(
            f"guard-incoming-config: BLOCKED — read error for {label}: {e}.",
            file=sys.stderr,
        )
        sys.exit(2)

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        print(
            f"guard-incoming-config: BLOCKED — {label} is not valid JSON "
            f"(line {e.lineno} col {e.colno}: {e.msg}). Fix or remove before continuing.",
            file=sys.stderr,
        )
        sys.exit(2)


# ── main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    cwd = Path.cwd()
    repo_root = _git_repo_root(cwd) or cwd

    settings = repo_root / ".claude" / "settings.json"
    mcp = repo_root / ".mcp.json"

    findings: list[Finding] = []
    _check_settings_json(settings, findings)
    _check_mcp_json(mcp, findings)

    if not findings:
        sys.exit(0)

    print("guard-incoming-config: BLOCKED — project config audit failed:", file=sys.stderr)
    for f in findings:
        print(f"  {f.path}: at {f.key}: {f.reason}", file=sys.stderr)
    print(
        "  Review and remove or sanitize these entries before continuing.",
        file=sys.stderr,
    )
    sys.exit(2)


if __name__ == "__main__":
    main()
