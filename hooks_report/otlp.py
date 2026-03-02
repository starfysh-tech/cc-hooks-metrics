"""OTLP/HTTP JSON export for hooks spans.

Zero external dependencies — uses stdlib urllib.request.
Activated by HOOKS_METRICS_OTLP_ENDPOINT env var.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from typing import TYPE_CHECKING

from . import config

if TYPE_CHECKING:
    from .spans import Span


def is_enabled() -> bool:
    """Return True if OTLP endpoint env var is set and non-empty."""
    return bool(os.environ.get(config.OTLP_ENDPOINT_VAR, "").strip())


def root_span_id_from_session(session_id: str) -> str:
    """Deterministic 16-char hex root span_id from session_id.

    Uses sha256 — collision-safe vs child span_ids (prefix_byte + row_id).
    Empty session_id → all-zeros, grouping pre-session-tracking data.
    """
    if not session_id:
        return "0" * 16
    return hashlib.sha256(session_id.encode()).hexdigest()[:16]


def _typed_value(v) -> dict:
    """Convert a Python value to OTLP AnyValue dict.

    bool checked before int (bool is a subclass of int in Python).
    intValue uses string repr per proto3 JSON mapping for int64.
    """
    if isinstance(v, bool):
        return {"boolValue": v}
    if isinstance(v, int):
        return {"intValue": str(v)}
    if isinstance(v, float):
        return {"doubleValue": v}
    return {"stringValue": str(v)}


def _attrs_to_otlp(attrs: dict) -> list[dict]:
    """Convert flat dict to OTLP KeyValue list."""
    return [{"key": k, "value": _typed_value(v)} for k, v in attrs.items()]


def _span_to_otlp(span: "Span", parent_span_id: str) -> dict:
    """Convert Span to OTLP JSON span dict.

    Timestamps are string representations per proto3 JSON for fixed64.
    parentSpanId is empty string for root spans.
    """
    return {
        "traceId": span.trace_id,
        "spanId": span.span_id,
        "parentSpanId": parent_span_id,
        "name": span.name,
        "kind": span.kind,
        "startTimeUnixNano": str(span.start_time_unix_nano),
        "endTimeUnixNano": str(span.end_time_unix_nano),
        "attributes": _attrs_to_otlp(span.attributes),
        "status": {"code": span.status_code},
    }


def _build_root_span(
    trace_id: str,
    root_span_id: str,
    session_id: str,
    children: list["Span"],
) -> dict:
    """Synthetic 'session' root span that parents all child spans in a trace.

    Timing: min(start) to max(end) of children.
    Status: ERROR (code=2) if any child is ERROR, else OK (code=1).
    Kind: 1 (INTERNAL) — session grouping is a local analysis concept.
    """
    start_ns = min(s.start_time_unix_nano for s in children)
    end_ns = max(s.end_time_unix_nano for s in children)
    has_error = any(s.status_code == 2 for s in children)
    return {
        "traceId": trace_id,
        "spanId": root_span_id,
        "parentSpanId": "",
        "name": "session",
        "kind": 1,
        "startTimeUnixNano": str(start_ns),
        "endTimeUnixNano": str(end_ns),
        "attributes": [
            {"key": "claude.session_id", "value": {"stringValue": session_id}},
        ],
        "status": {"code": 2 if has_error else 1},
    }


def build_otlp_payload(spans: list["Span"]) -> dict:
    """Build OTLP/HTTP JSON payload from a list of Spans.

    Groups spans by trace_id. Creates a synthetic root span per trace.
    Structure: ResourceSpans > ScopeSpans > Span[].
    """
    if not spans:
        return {"resourceSpans": []}

    by_trace: dict[str, list["Span"]] = {}
    for span in spans:
        by_trace.setdefault(span.trace_id, []).append(span)

    otlp_spans = []
    for trace_id, trace_spans in by_trace.items():
        session_id = trace_spans[0].session_id if trace_spans else ""
        root_span_id = root_span_id_from_session(session_id)
        otlp_spans.append(_build_root_span(trace_id, root_span_id, session_id, trace_spans))
        for span in trace_spans:
            otlp_spans.append(_span_to_otlp(span, parent_span_id=root_span_id))

    resource_attrs = _attrs_to_otlp({
        "service.name": config.OTLP_SERVICE_NAME,
        "service.version": config.OTLP_SERVICE_VERSION,
    })
    return {
        "resourceSpans": [
            {
                "resource": {"attributes": resource_attrs},
                "scopeSpans": [
                    {
                        "scope": {"name": config.OTLP_SCOPE_NAME},
                        "spans": otlp_spans,
                    }
                ],
            }
        ]
    }


def _parse_headers(header_str: str) -> dict:
    """Parse 'key=value,key2=value2' header string into a dict.

    Handles spaces around delimiters. Warns to stderr on malformed entries.
    """
    headers: dict[str, str] = {}
    for part in header_str.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            k, _, v = part.partition("=")
            headers[k.strip()] = v.strip()
        else:
            print(f"warn: otlp: malformed header entry skipped: {part!r}", file=sys.stderr)
    return headers


def send_spans(spans: list["Span"]) -> int:
    """POST spans to OTLP endpoint. Returns count exported.

    Network errors and HTTP errors are printed to stderr; never raises.
    Handles partial success (partialSuccess.rejectedSpans) from backend.
    """
    if not spans:
        return 0

    endpoint = os.environ.get(config.OTLP_ENDPOINT_VAR, "").rstrip("/")
    if not endpoint:
        return 0

    url = f"{endpoint}/v1/traces"
    try:
        payload = build_otlp_payload(spans)
        total = sum(len(ss["spans"]) for rs in payload["resourceSpans"] for ss in rs["scopeSpans"])
        body = json.dumps(payload).encode()

        headers = {"Content-Type": "application/json"}
        header_str = os.environ.get(config.OTLP_HEADERS_VAR, "")
        if header_str:
            headers.update(_parse_headers(header_str))

        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=config.OTLP_TIMEOUT_S) as resp:
            resp_body = resp.read().decode(errors="replace")
            try:
                resp_json = json.loads(resp_body)
                raw = (resp_json.get("partialSuccess") or {}).get("rejectedSpans", 0)
                rejected = int(raw)
                if rejected > 0:
                    print(
                        f"warn: otlp: {rejected}/{total} spans rejected by backend",
                        file=sys.stderr,
                    )
                    return max(0, total - rejected)
            except (json.JSONDecodeError, AttributeError, ValueError, TypeError):
                pass  # empty, non-JSON, or non-integer rejectedSpans — treat as full success
            return total
    except urllib.error.HTTPError as e:
        body_preview = e.read(200).decode(errors="replace")
        print(f"warn: otlp: HTTP {e.code} from {url}: {body_preview!r}", file=sys.stderr)
        return 0
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        print(f"warn: otlp: network error sending to {url}: {e}", file=sys.stderr)
        return 0
    except Exception as e:
        print(f"warn: otlp: unexpected error: {e}", file=sys.stderr)
        return 0
