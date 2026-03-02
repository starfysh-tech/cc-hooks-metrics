# Phase 5: Optional OTLP Backend Export — Design

**Date**: 2026-03-02
**Status**: Implemented — PR #5 (`phase5-otlp`)

## Problem

Phases 1-4 built a local span model (`spans.py`) and JSON export (`--export-spans`), but there's no way to send spans to an observability backend (SigNoz, Grafana Tempo, Jaeger) for trace visualization and correlation with Claude Code's native telemetry.

The original Phase 5 plan used `opentelemetry-sdk` which has two problems: the SDK generates its own random trace/span IDs (losing our deterministic session-based IDs), and it adds ~5MB of dependencies for what amounts to an HTTP POST.

## Design Decisions

- **Direct OTLP/HTTP JSON** over `opentelemetry-sdk` — zero external dependencies (stdlib `urllib.request`), full control over trace/span IDs
- **Synthetic session root spans** — one `"session"` root span per `trace_id`, hook/tool spans become children via `parentSpanId`. Gives waterfall views in backends.
- **Opt-in via env var** — `HOOKS_METRICS_OTLP_ENDPOINT` triggers OTLP send as side-effect of `--export-spans`
- **Proper deterministic IDs** — our 32-char hex trace_id and 16-char hex span_id injected directly into OTLP payload (already OTel-compatible sizes)

## Architecture

```
Span objects (from spans.py)
  → otlp.build_otlp_payload()     # group by trace, synth root spans, OTLP JSON format
  → otlp.send_spans()             # POST to {endpoint}/v1/traces via urllib
  → stderr: "otlp: exported N/M spans"
  → stdout: JSON (unchanged --export-spans output)
```

## Scope

**In scope**: `otlp.py` (new), Span dataclass `session_id` field, `__main__.py` dispatch, config constants
**Out of scope**: docs file (follow-up), retry logic, batching, new CLI flags, test coverage
