"""Unit tests for hooks_report.otlp — pure functions and send_spans mock paths."""

import hashlib
import json
from unittest.mock import MagicMock, patch

from hooks_report.otlp import (
    _parse_headers,
    _typed_value,
    build_otlp_payload,
    is_enabled,
    root_span_id_from_session,
    send_spans,
)
from hooks_report.spans import Span


# ── root_span_id_from_session ──────────────────────────────────────────────────

def test_root_span_id_empty_session():
    assert root_span_id_from_session("") == "0" * 16


def test_root_span_id_deterministic():
    sid = "abc-session-123"
    expected = hashlib.sha256(sid.encode()).hexdigest()[:16]
    assert root_span_id_from_session(sid) == expected
    assert root_span_id_from_session(sid) == root_span_id_from_session(sid)


def test_root_span_id_length():
    assert len(root_span_id_from_session("any-session")) == 16


# ── _typed_value ──────────────────────────────────────────────────────────────

def test_typed_value_bool_before_int():
    # True/False are bool AND int in Python — must resolve to boolValue
    assert _typed_value(True) == {"boolValue": True}
    assert _typed_value(False) == {"boolValue": False}


def test_typed_value_int():
    # proto3 encodes int64 as string
    assert _typed_value(42) == {"intValue": "42"}
    assert _typed_value(0) == {"intValue": "0"}


def test_typed_value_float():
    assert _typed_value(1.5) == {"doubleValue": 1.5}


def test_typed_value_string():
    assert _typed_value("hello") == {"stringValue": "hello"}
    assert _typed_value("") == {"stringValue": ""}


# ── _parse_headers ─────────────────────────────────────────────────────────────

def test_parse_headers_empty():
    assert _parse_headers("") == {}


def test_parse_headers_basic():
    result = _parse_headers("Authorization=Bearer token123,X-Tenant=my-org")
    assert result == {"Authorization": "Bearer token123", "X-Tenant": "my-org"}


def test_parse_headers_spaces():
    result = _parse_headers("  key = value , key2 = value2 ")
    assert result["key"] == "value"
    assert result["key2"] == "value2"


def test_parse_headers_malformed_entry_skipped():
    result = _parse_headers("good=val,no-equals-sign,another=ok")
    assert "no-equals-sign" not in result
    assert result["good"] == "val"
    assert result["another"] == "ok"


# ── build_otlp_payload ────────────────────────────────────────────────────────

def _make_span(session_id: str, span_id: str, name: str = "hook.test") -> Span:
    return Span(
        trace_id=hashlib.sha256(session_id.encode()).hexdigest()[:32] if session_id else "0" * 32,
        span_id=span_id,
        name=name,
        kind=3,
        start_time_unix_nano=1_000_000_000,
        end_time_unix_nano=2_000_000_000,
        status_code=1,
        attributes={"hook.step": "test", "hook.exit_code": 0},
        session_id=session_id,
    )


def test_build_otlp_payload_adds_root_span():
    spans = [_make_span("sess-1", "1111111111111111")]
    payload = build_otlp_payload(spans)
    otlp_spans = payload["resourceSpans"][0]["scopeSpans"][0]["spans"]
    # 1 input span + 1 root span
    assert len(otlp_spans) == 2


def test_build_otlp_payload_root_span_name():
    spans = [_make_span("sess-1", "1111111111111111")]
    payload = build_otlp_payload(spans)
    otlp_spans = payload["resourceSpans"][0]["scopeSpans"][0]["spans"]
    root = next(s for s in otlp_spans if s["name"] == "session")
    assert root["spanId"] == root_span_id_from_session("sess-1")


def test_build_otlp_payload_root_span_has_session_attribute():
    spans = [_make_span("sess-abc", "1111111111111111")]
    payload = build_otlp_payload(spans)
    otlp_spans = payload["resourceSpans"][0]["scopeSpans"][0]["spans"]
    root = next(s for s in otlp_spans if s["name"] == "session")
    attr_keys = {a["key"] for a in root["attributes"]}
    assert "claude.session_id" in attr_keys


def test_build_otlp_payload_child_has_parent():
    spans = [_make_span("sess-1", "1111111111111111")]
    payload = build_otlp_payload(spans)
    otlp_spans = payload["resourceSpans"][0]["scopeSpans"][0]["spans"]
    child = next(s for s in otlp_spans if s["name"] != "session")
    assert child["parentSpanId"] == root_span_id_from_session("sess-1")


def test_build_otlp_payload_two_sessions_two_roots():
    spans = [
        _make_span("sess-1", "1111111111111111"),
        _make_span("sess-2", "2222222222222222"),
    ]
    payload = build_otlp_payload(spans)
    otlp_spans = payload["resourceSpans"][0]["scopeSpans"][0]["spans"]
    # 2 input spans + 2 root spans
    assert len(otlp_spans) == 4


def test_build_otlp_payload_attribute_serialization():
    spans = [_make_span("sess-1", "1111111111111111")]
    payload = build_otlp_payload(spans)
    otlp_spans = payload["resourceSpans"][0]["scopeSpans"][0]["spans"]
    child = next(s for s in otlp_spans if s["name"] != "session")
    attr_keys = {a["key"] for a in child["attributes"]}
    assert "hook.step" in attr_keys
    # exit_code 0 → intValue "0"
    exit_attr = next(a for a in child["attributes"] if a["key"] == "hook.exit_code")
    assert exit_attr["value"] == {"intValue": "0"}


def test_build_otlp_payload_times_as_strings():
    spans = [_make_span("sess-1", "1111111111111111")]
    payload = build_otlp_payload(spans)
    otlp_spans = payload["resourceSpans"][0]["scopeSpans"][0]["spans"]
    child = next(s for s in otlp_spans if s["name"] != "session")
    assert isinstance(child["startTimeUnixNano"], str)
    assert isinstance(child["endTimeUnixNano"], str)


def test_build_otlp_payload_empty_spans():
    payload = build_otlp_payload([])
    assert payload == {"resourceSpans": []}


# ── send_spans ────────────────────────────────────────────────────────────────

ENDPOINT = "http://localhost:4318"


def _mock_response(body: dict):
    mock = MagicMock()
    mock.read.return_value = json.dumps(body).encode()
    mock.__enter__ = lambda s: s
    mock.__exit__ = MagicMock(return_value=False)
    return mock


def test_send_spans_full_success():
    spans = [_make_span("sess-1", "1111111111111111")]
    # 1 input + 1 root = 2 total in payload
    mock_resp = _mock_response({})
    env = {"HOOKS_METRICS_OTLP_ENDPOINT": ENDPOINT}
    with patch.dict("os.environ", env), patch("urllib.request.urlopen", return_value=mock_resp):
        count = send_spans(spans)
    assert count == 2


def test_send_spans_partial_rejection():
    spans = [_make_span("sess-1", "1111111111111111")]
    mock_resp = _mock_response({"partialSuccess": {"rejectedSpans": 1}})
    env = {"HOOKS_METRICS_OTLP_ENDPOINT": ENDPOINT}
    with patch.dict("os.environ", env), patch("urllib.request.urlopen", return_value=mock_resp):
        count = send_spans(spans)
    assert count == 1  # 2 total - 1 rejected


def test_send_spans_no_endpoint_returns_zero():
    spans = [_make_span("sess-1", "1111111111111111")]
    with patch.dict("os.environ", {}, clear=True):
        count = send_spans(spans)
    assert count == 0


def test_send_spans_empty_spans_returns_zero():
    env = {"HOOKS_METRICS_OTLP_ENDPOINT": ENDPOINT}
    with patch.dict("os.environ", env):
        count = send_spans([])
    assert count == 0


def test_send_spans_http_error_returns_zero(capsys):
    import io
    import urllib.error
    spans = [_make_span("sess-1", "1111111111111111")]
    env = {"HOOKS_METRICS_OTLP_ENDPOINT": ENDPOINT}
    err = urllib.error.HTTPError(ENDPOINT, 500, "Internal Server Error", MagicMock(), io.BytesIO(b"server error"))
    with patch.dict("os.environ", env), patch("urllib.request.urlopen", side_effect=err):
        count = send_spans(spans)
    assert count == 0
    assert "warn: otlp:" in capsys.readouterr().err


def test_send_spans_network_error_returns_zero(capsys):
    import urllib.error
    spans = [_make_span("sess-1", "1111111111111111")]
    env = {"HOOKS_METRICS_OTLP_ENDPOINT": ENDPOINT}
    with patch.dict("os.environ", env), patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
        count = send_spans(spans)
    assert count == 0
    assert "warn: otlp:" in capsys.readouterr().err


def test_send_spans_malformed_rejected_count_returns_total(capsys):
    """Non-integer rejectedSpans is ignored; full total returned."""
    spans = [_make_span("sess-1", "1111111111111111")]
    mock_resp = _mock_response({"partialSuccess": {"rejectedSpans": "five"}})
    env = {"HOOKS_METRICS_OTLP_ENDPOINT": ENDPOINT}
    with patch.dict("os.environ", env), patch("urllib.request.urlopen", return_value=mock_resp):
        count = send_spans(spans)
    assert count == 2  # 1 input + 1 root; malformed rejection → treat as full success


def test_send_spans_excess_rejection_clamped_to_zero():
    """rejectedSpans > total is clamped to zero, not negative."""
    spans = [_make_span("sess-1", "1111111111111111")]
    # total = 2 (1 input + 1 root), backend claims 10 rejected
    mock_resp = _mock_response({"partialSuccess": {"rejectedSpans": 10}})
    env = {"HOOKS_METRICS_OTLP_ENDPOINT": ENDPOINT}
    with patch.dict("os.environ", env), patch("urllib.request.urlopen", return_value=mock_resp):
        count = send_spans(spans)
    assert count == 0


def test_send_spans_never_raises_on_payload_error(capsys):
    """send_spans never raises even if span data causes an internal error."""
    bad_span = Span(
        trace_id="a" * 32, span_id="b" * 16, name="hook.bad",
        kind=3, start_time_unix_nano=1_000_000_000, end_time_unix_nano=2_000_000_000,
        status_code=1, attributes=None, session_id="sess-1",
    )
    env = {"HOOKS_METRICS_OTLP_ENDPOINT": ENDPOINT}
    with patch.dict("os.environ", env):
        count = send_spans([bad_span])
    assert count == 0
    assert "warn: otlp:" in capsys.readouterr().err


# ── is_enabled ────────────────────────────────────────────────────────────────

def test_is_enabled_with_endpoint():
    with patch.dict("os.environ", {"HOOKS_METRICS_OTLP_ENDPOINT": ENDPOINT}, clear=True):
        assert is_enabled() is True


def test_is_enabled_without_endpoint():
    with patch.dict("os.environ", {}, clear=True):
        assert is_enabled() is False


# ── root span behavioral tests ────────────────────────────────────────────────

def test_build_otlp_payload_root_span_error_when_any_child_errors():
    """Root span is ERROR when any child has status_code 2."""
    trace_id = hashlib.sha256(b"sess-err").hexdigest()[:32]
    ok_span = Span(
        trace_id=trace_id, span_id="a" * 16, name="hook.ok",
        kind=3, start_time_unix_nano=1_000_000_000, end_time_unix_nano=2_000_000_000,
        status_code=1, attributes={}, session_id="sess-err",
    )
    err_span = Span(
        trace_id=trace_id, span_id="b" * 16, name="hook.fail",
        kind=3, start_time_unix_nano=1_000_000_000, end_time_unix_nano=2_000_000_000,
        status_code=2, attributes={}, session_id="sess-err",
    )
    payload = build_otlp_payload([ok_span, err_span])
    otlp_spans = payload["resourceSpans"][0]["scopeSpans"][0]["spans"]
    root = next(s for s in otlp_spans if s["name"] == "session")
    assert root["status"]["code"] == 2


def test_build_otlp_payload_root_span_ok_when_all_children_ok():
    """Root span is OK when all children are OK."""
    trace_id = hashlib.sha256(b"sess-ok").hexdigest()[:32]
    spans = [
        Span(trace_id=trace_id, span_id="a" * 16, name="hook.a",
             kind=3, start_time_unix_nano=1_000_000_000, end_time_unix_nano=2_000_000_000,
             status_code=1, attributes={}, session_id="sess-ok"),
        Span(trace_id=trace_id, span_id="b" * 16, name="hook.b",
             kind=3, start_time_unix_nano=2_000_000_000, end_time_unix_nano=3_000_000_000,
             status_code=1, attributes={}, session_id="sess-ok"),
    ]
    payload = build_otlp_payload(spans)
    otlp_spans = payload["resourceSpans"][0]["scopeSpans"][0]["spans"]
    root = next(s for s in otlp_spans if s["name"] == "session")
    assert root["status"]["code"] == 1


def test_build_otlp_payload_root_span_timing_envelope():
    """Root span covers min(start) to max(end) of children."""
    trace_id = hashlib.sha256(b"sess-time").hexdigest()[:32]
    early = Span(
        trace_id=trace_id, span_id="a" * 16, name="hook.early",
        kind=3, start_time_unix_nano=1_000_000_000, end_time_unix_nano=2_000_000_000,
        status_code=1, attributes={}, session_id="sess-time",
    )
    late = Span(
        trace_id=trace_id, span_id="b" * 16, name="hook.late",
        kind=3, start_time_unix_nano=3_000_000_000, end_time_unix_nano=5_000_000_000,
        status_code=1, attributes={}, session_id="sess-time",
    )
    payload = build_otlp_payload([early, late])
    otlp_spans = payload["resourceSpans"][0]["scopeSpans"][0]["spans"]
    root = next(s for s in otlp_spans if s["name"] == "session")
    assert root["startTimeUnixNano"] == "1000000000"
    assert root["endTimeUnixNano"] == "5000000000"
