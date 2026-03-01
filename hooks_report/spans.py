from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class Span:
    """Custom span record for claude.hooks.spans/v1. No external SDK dependency."""
    trace_id: str           # 32-char hex; derived from session_id via SHA256
    span_id: str            # 16-char hex; prefix byte + row id (human-readable, sort-stable)
    name: str               # "hook.{step}" or "tool.{tool_name}"
    kind: int               # 1=INTERNAL (hooks), 3=CLIENT (tools)
    start_time_unix_nano: int
    end_time_unix_nano: int
    status_code: int        # 0=UNSET, 1=OK, 2=ERROR
    attributes: dict


def trace_id_from_session(session_id: str) -> str:
    """Deterministic 32-char hex trace_id from session_id."""
    if not session_id:
        return "0" * 32
    return hashlib.sha256(session_id.encode()).hexdigest()[:32]


def span_id_from_row_id(row_id: int, prefix: str = "h") -> str:
    """Deterministic 16-char hex span_id. Prefix byte + row id — human-readable and sort-stable."""
    prefix_byte = ord(prefix[0])
    return f"{prefix_byte:02x}{row_id:014x}"


def _ts_to_nanos(ts_str: str) -> int:
    """Convert SQLite TEXT timestamp to Unix nanoseconds. Returns 0 on corrupt input."""
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        try:
            dt = datetime.fromisoformat(ts_str).replace(tzinfo=timezone.utc)
        except (ValueError, AttributeError):
            return 0
    return int(dt.timestamp() * 1_000_000_000)


def _redact_tool_input(input_json: str) -> str:
    """Return privacy-safe summary of tool input. Never returns file contents."""
    try:
        inp = json.loads(input_json)
    except (json.JSONDecodeError, TypeError):
        return "{}"
    safe: dict = {}
    if "command" in inp:
        cmd_val = inp["command"]
        safe["command"] = (str(cmd_val).split() or [""])[0] if cmd_val else ""
    if "file_path" in inp:
        fp = inp["file_path"]
        safe["file_path"] = os.path.basename(str(fp)) if fp else ""
    if "tool_name" in inp:
        safe["tool_name"] = inp["tool_name"]
    return json.dumps(safe)


def _hash_host(host: str) -> str:
    """One-way hash of hostname — identifies machine type without exposing identity."""
    return hashlib.sha256(host.encode()).hexdigest()[:12]


def hook_metric_to_span(row: tuple, redact: bool = True) -> Span:
    """Convert a hook_metrics row to a Span.

    Row order: id, ts, hook, step, cmd, exit_code, duration_ms,
               real_s, user_s, sys_s, branch, sha, host, repo, session
    """
    row_id, ts, hook, step, cmd, exit_code, duration_ms = row[:7]
    branch, sha, host, repo, session = row[10:15]

    start_ns = _ts_to_nanos(ts)
    if start_ns == 0:
        raise ValueError(f"corrupt timestamp: {ts!r}")
    dur = int(duration_ms or 0)
    end_ns = start_ns + dur * 1_000_000

    status_code = 2 if exit_code != 0 else 1  # ERROR or OK

    repo_display = os.path.basename(repo.rstrip("/")) if repo else ""

    attrs: dict = {
        "hook.step": step,
        "hook.event": hook,
        "hook.exit_code": exit_code,
        "hook.duration_ms": dur,
        "vcs.branch": branch,
        "vcs.commit_sha": sha,
        "vcs.repository": repo_display if redact else repo,
        "host.name": _hash_host(host) if redact else host,
    }
    if not redact:
        attrs["hook.cmd"] = cmd

    return Span(
        trace_id=trace_id_from_session(session),
        span_id=span_id_from_row_id(row_id, "h"),
        name=f"hook.{step}",
        kind=1,
        start_time_unix_nano=start_ns,
        end_time_unix_nano=end_ns,
        status_code=status_code,
        attributes=attrs,
    )


def audit_event_to_span(row: tuple, redact: bool = True) -> Span:
    """Convert an audit_events row to a Span.

    Row order: id, ts, session, tool, input
    """
    row_id, ts, session, tool, input_json = row

    start_ns = _ts_to_nanos(ts)
    end_ns = start_ns  # tool-use events have no duration

    tool_input = _redact_tool_input(input_json) if redact else input_json

    attrs: dict = {
        "tool.name": tool,
        "tool.input": tool_input,
    }

    return Span(
        trace_id=trace_id_from_session(session),
        span_id=span_id_from_row_id(row_id, "a"),
        name=f"tool.{tool}",
        kind=3,
        start_time_unix_nano=start_ns,
        end_time_unix_nano=end_ns,
        status_code=2 if tool.startswith("PostToolUseFailure:") else 1,
        attributes=attrs,
    )


def spans_to_dict(spans: list[Span]) -> dict:
    """Serialize spans to claude.hooks.spans/v1 JSON.

    Flat structure designed for human readability and LLM analysis.
    Not OTLP wire format — defer camelCase + typed attribute wrappers to Phase 5.
    """
    return {
        "schema": "claude.hooks.spans/v1",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "spans": [
            {
                "trace_id": s.trace_id,
                "span_id": s.span_id,
                "name": s.name,
                "kind": s.kind,
                "start_ns": s.start_time_unix_nano,
                "end_ns": s.end_time_unix_nano,
                "status": s.status_code,
                "attributes": s.attributes,
            }
            for s in spans
        ],
    }
