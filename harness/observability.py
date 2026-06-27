"""OpenTelemetry tracing for the orchestrator (exported to self-hosted Langfuse via OTLP).

Vendor-neutral: the graph emits OTel spans; Langfuse is just an OTLP endpoint. If tracing
is not configured (no Langfuse keys), ``get_tracer`` returns the OTel no-op tracer and spans
become harmless no-ops — instrumentation never breaks a run (graceful degrade).
"""
from __future__ import annotations

import base64
import os
from typing import TYPE_CHECKING

from opentelemetry import trace

if TYPE_CHECKING:
    from opentelemetry.trace import Tracer

_TRACER_NAME = "harness"


def get_tracer() -> Tracer:
    """The harness tracer. No-op unless a TracerProvider has been configured."""
    return trace.get_tracer(_TRACER_NAME)


def configure_tracing(*, service_name: str = "agentic-harness") -> bool:
    """Wire global OTel tracing to self-hosted Langfuse via OTLP. Best-effort and idempotent.

    Returns True if a real provider is now active, False if skipped (no Langfuse keys) or if
    setup failed. Never raises — a broken/absent collector must not break the run.
    """
    host = os.environ.get("LANGFUSE_HOST")
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY")
    if not (host and public_key and secret_key):
        return False
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        if isinstance(trace.get_tracer_provider(), TracerProvider):
            return True  # already configured

        auth = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
        exporter = OTLPSpanExporter(
            endpoint=f"{host.rstrip('/')}/api/public/otel/v1/traces",
            headers={"Authorization": f"Basic {auth}"},
        )
        provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        return True
    except Exception:
        return False
