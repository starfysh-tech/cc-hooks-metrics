---
title: "Phase 5: Optional OTEL Backend Export"
phase: 5
date: 2026-02-28
dependencies: "Phase 1 (spans.py Span dataclass + export_spans)"
status: ready
---

# Phase 5: Optional OTEL Backend Export

## Overview

Add optional OTLP export that sends local hook spans to a remote observability backend. This is strictly opt-in: controlled by the `HOOKS_METRICS_OTLP_ENDPOINT` env var, with `opentelemetry-sdk` as an optional dependency. When enabled, converts Phase 1 `Span` objects to OTLP format and exports them via HTTP. Includes documentation for setting up SigNoz or Grafana as a backend and correlating with Claude Code's native telemetry.

## Dependencies

- **Phase 1 `spans.py`**: Provides the `Span` dataclass and `export_spans()` function that produces span data from `hook_metrics` and `audit_events` tables
- **Optional Python packages**: `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http` -- only required if OTLP export is enabled. The tool works without them installed.
- **No changes to bash scripts or database schema**

## Implementation

### 5a: New file `hooks_report/otlp.py`

Create `hooks_report/otlp.py` (~80 lines). The file uses a try/except import pattern so the rest of the tool works without the opentelemetry packages installed.

```python
"""Optional OTLP span exporter.

Requires: pip install opentelemetry-sdk opentelemetry-exporter-otlp-proto-http

Activated by setting HOOKS_METRICS_OTLP_ENDPOINT env var.
"""
from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .spans import Span

OTLP_ENDPOINT_VAR = "HOOKS_METRICS_OTLP_ENDPOINT"


def _get_endpoint() -> str | None:
    """Return OTLP endpoint from env, or None if not configured."""
    return os.environ.get(OTLP_ENDPOINT_VAR)


def is_enabled() -> bool:
    """Check if OTLP export is configured."""
    return bool(_get_endpoint())


def send_spans_otlp(spans: list[Span], endpoint: str | None = None) -> int:
    """Convert Span objects to OTLP format and send to the configured endpoint.

    Args:
        spans: List of Span dataclass instances from spans.py
        endpoint: OTLP endpoint URL. Falls back to HOOKS_METRICS_OTLP_ENDPOINT env var.

    Returns:
        Number of spans successfully exported.

    Raises:
        ImportError: If opentelemetry packages are not installed.
        RuntimeError: If no endpoint is configured.
    """
    endpoint = endpoint or _get_endpoint()
    if not endpoint:
        raise RuntimeError(
            f"No OTLP endpoint configured. Set {OTLP_ENDPOINT_VAR} env var "
            "or pass endpoint argument."
        )

    try:
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.trace import StatusCode
        import opentelemetry.trace as trace_api
    except ImportError:
        print(
            "Error: opentelemetry packages not installed.\n"
            "Install with: pip install opentelemetry-sdk opentelemetry-exporter-otlp-proto-http",
            file=sys.stderr,
        )
        raise

    # Configure exporter
    resource = Resource.create({
        "service.name": "claude-hooks",
        "service.version": "1.0.0",
    })
    exporter = OTLPSpanExporter(endpoint=f"{endpoint.rstrip('/')}/v1/traces")
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    tracer = provider.get_tracer("claude.hooks")
    exported = 0

    for span in spans:
        # Create OTLP span from our Span dataclass
        otel_span = tracer.start_span(
            name=span.name,
            attributes=span.attributes,
        )
        if span.status_code == "ERROR":
            otel_span.set_status(StatusCode.ERROR)
        else:
            otel_span.set_status(StatusCode.OK)

        # Set trace/span IDs from our deterministic values
        otel_span.set_attribute("hook.trace_id", span.trace_id)
        otel_span.set_attribute("hook.span_id", span.span_id)
        if span.parent_span_id:
            otel_span.set_attribute("hook.parent_span_id", span.parent_span_id)
        otel_span.set_attribute("hook.duration_ms", span.duration_ms)

        otel_span.end()
        exported += 1

    # Flush and shutdown
    provider.force_flush()
    provider.shutdown()

    return exported
```

**Design notes**:
- `SimpleSpanProcessor` is used instead of `BatchSpanProcessor` because we export in a single batch at CLI invocation time (not a long-running process)
- The OTLP exporter sends to `{endpoint}/v1/traces` following the OTLP HTTP convention
- `Resource` sets `service.name: claude-hooks` so spans are identifiable in the backend
- Trace/span IDs from our deterministic model are stored as attributes since the OTEL SDK generates its own IDs. A future optimization could use the SDK's `ReadableSpan` interface to inject our IDs directly.

### 5b: Dispatch in `hooks_report/__main__.py`

Add conditional OTLP send after span export. Insert after the `--export-spans` dispatch block (from Phase 1):

```python
if args.export_spans:
    from .spans import export_spans
    spans_data = export_spans(db, hours=args.span_hours, redact=not args.no_redact)

    # Conditionally send to OTLP backend
    from .otlp import is_enabled
    if is_enabled():
        from .otlp import send_spans_otlp
        from .spans import hook_metric_to_span, audit_event_to_span
        spans_raw = db.spans_raw(hours=args.span_hours)
        span_objects = (
            [hook_metric_to_span(r) for r in spans_raw[0]]
            + [audit_event_to_span(r) for r in spans_raw[1]]
        )
        count = send_spans_otlp(span_objects)
        print(f"Exported {count} spans to OTLP endpoint", file=sys.stderr)

    import json
    print(json.dumps(spans_data, indent=2))
    return
```

This keeps OTLP export as a side effect of `--export-spans` when the endpoint is configured. No additional CLI flag is needed -- the env var acts as the toggle.

### 5c: Documentation deliverable — `docs/otel-backend-setup.md`

**Do not create this file in this phase -- document the outline here.** The file should be created during implementation and cover:

#### Recommended: SigNoz single-node

```markdown
## SigNoz (Recommended)

Simplest single-node setup for local observability.

### Quick start
docker run -d --name signoz \
  -p 4318:4318 \    # OTLP HTTP receiver
  -p 3301:3301 \    # UI
  signoz/signoz:latest

### Configure
export HOOKS_METRICS_OTLP_ENDPOINT=http://localhost:4318

### Verify
~/.claude/hooks/hooks-report.sh --export-spans
# Check SigNoz UI at http://localhost:3301 -> Traces
```

#### Alternative: Grafana stack

```markdown
## Grafana Stack (Advanced)

For users who already run Grafana or want more customization.

Components:
- Grafana Alloy (collector) — receives OTLP, routes to Tempo
- Grafana Tempo (trace backend) — stores and queries traces
- Grafana Loki (optional) — log correlation

### docker-compose.yml outline
- alloy: receives on :4318, forwards to tempo:4317
- tempo: stores traces, queryable from grafana
- grafana: dashboards at :3000

### Example dashboards
- Sessions waterfall: trace view grouped by session_id
- Failure heatmap: step x hour grid colored by failure rate
- Cost vs hook time: overlay hook overhead on session duration
```

#### Claude Code native telemetry correlation

```markdown
## Correlating with Claude Code Telemetry

Claude Code has built-in OTLP export (separate from hooks):

export CLAUDE_CODE_ENABLE_TELEMETRY=1
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318

This sends Claude's own model-level metrics (token usage, API latency, etc.)
to the same backend. Both data sources share `claude.session_id` as a
common attribute, enabling correlated views:

- Hook spans appear alongside Claude API spans in the same trace
- Compare hook overhead vs model latency per session
- Identify sessions where hooks consumed disproportionate time

### Shared attribute
Hook spans: `attributes.claude.session_id` (from hook-metrics.sh extraction)
Claude spans: `attributes.claude.session_id` (from native telemetry)

Query in SigNoz/Tempo:
  { .claude.session_id = "<session-uuid>" }
```

### 5d: Optional dependency installation

The opentelemetry packages are not required for normal operation. Document installation:

```bash
# Only needed if you want to export to an OTLP backend
pip install opentelemetry-sdk opentelemetry-exporter-otlp-proto-http

# Verify installation
python3 -c "from opentelemetry.sdk.trace import TracerProvider; print('OK')"

# Configure endpoint
export HOOKS_METRICS_OTLP_ENDPOINT=http://localhost:4318

# Test export
~/.claude/hooks/hooks-report.sh --export-spans
```

If the packages are not installed and the env var is not set, the tool behaves identically to pre-Phase-5 -- no errors, no warnings, no changes.

If the env var IS set but packages are NOT installed, `send_spans_otlp()` raises `ImportError` with a clear installation message. This is intentional -- the user explicitly opted in but is missing the dependency.

## Files Changed

| File | Change | Notes |
|------|--------|-------|
| `hooks_report/otlp.py` | **NEW** (~80 lines) | `is_enabled()`, `send_spans_otlp()` with optional import pattern |
| `hooks_report/__main__.py` | +~10 lines | Conditional OTLP send after `--export-spans` dispatch |
| `docs/otel-backend-setup.md` | **NEW** (documentation) | Backend setup guide -- outline in this plan, file created during implementation |

## Verification

```bash
# Verify module loads without opentelemetry installed
python3 -c "from hooks_report.otlp import is_enabled; print(f'enabled={is_enabled()}')"
# Should print: enabled=False (no env var set)

# Verify graceful behavior without packages or env var
~/.claude/hooks/hooks-report.sh --export-spans --span-hours 1 | head -5
# Should output JSON normally, no OTLP errors

# Test with env var but no packages (should fail with clear message)
HOOKS_METRICS_OTLP_ENDPOINT=http://localhost:4318 \
  ~/.claude/hooks/hooks-report.sh --export-spans 2>&1 | grep "opentelemetry"
# Should print: "Error: opentelemetry packages not installed."

# Full integration test (requires SigNoz or similar running on :4318)
pip install opentelemetry-sdk opentelemetry-exporter-otlp-proto-http
export HOOKS_METRICS_OTLP_ENDPOINT=http://localhost:4318
~/.claude/hooks/hooks-report.sh --export-spans --span-hours 24
# Check stderr for "Exported N spans to OTLP endpoint"
# Check backend UI for traces with service.name = "claude-hooks"

# Verify Claude Code correlation (requires CLAUDE_CODE_ENABLE_TELEMETRY=1)
# In SigNoz/Tempo, query: { .service.name = "claude-hooks" }
# Verify claude.session_id attribute is present on exported spans
```

## Risks & Notes

- **Optional dependency size**: `opentelemetry-sdk` + exporter adds ~5MB to the Python environment. This is why it is optional -- most users will only use local JSON export.
- **OTLP endpoint reliability**: If the backend is down or unreachable, `send_spans_otlp()` will block briefly then raise. This happens during `--export-spans` (explicit user action), not during hook execution, so it does not affect Claude Code responsiveness.
- **Trace ID mapping limitation**: The OTEL SDK generates its own trace/span IDs. Our deterministic IDs (from `trace_id_from_session()` in Phase 1) are stored as attributes (`hook.trace_id`, `hook.span_id`) rather than overriding the SDK's IDs. This means backend trace views use the SDK's IDs, not ours. A future enhancement could use `ReadableSpan` to inject our IDs for true trace continuity.
- **`SimpleSpanProcessor` vs `BatchSpanProcessor`**: We use `SimpleSpanProcessor` because the export is a one-shot CLI operation, not a long-running process. Batch would buffer and risk losing spans on process exit.
- **Phase 5 is explicitly optional**: The entire OTLP export feature can be skipped. All other phases (1-4) work independently. Users who never set `HOOKS_METRICS_OTLP_ENDPOINT` and never install the opentelemetry packages see zero behavioral change.
- **Security**: The OTLP endpoint receives span data that includes step names, durations, exit codes, and redacted tool inputs. Ensure the endpoint is on a trusted network (localhost or VPN). The `--no-redact` flag (from Phase 1) controls whether tool inputs are redacted before export.
