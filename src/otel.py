"""opentelemetry setup + genai semantic conventions for llm/rag spans.

References the experimental semantic conventions for GenAI (mid-2024):
  gen_ai.system = "aws.bedrock"
  gen_ai.request.model
  gen_ai.request.max_tokens
  gen_ai.request.temperature
  gen_ai.response.finish_reasons
  gen_ai.usage.input_tokens
  gen_ai.usage.output_tokens

Also emits app-specific attrs:
  rag.retrieval.hit_ids
  rag.retrieval.k
  rag.redaction.n
  rag.guardrail.prompt_verdict
  rag.guardrail.response_verdict
  rag.acl.user_id
  rag.acl.org_id
  rag.acl.roles
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource, SERVICE_NAME
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter


_TRACER_NAME = "compliance-rag"
_provider: TracerProvider | None = None


def init_tracing(service_name: str | None = None, endpoint: str | None = None) -> None:
    global _provider
    if _provider is not None:
        return
    resource = Resource.create(
        {SERVICE_NAME: service_name or os.environ.get("OTEL_SERVICE_NAME", _TRACER_NAME)}
    )
    _provider = TracerProvider(resource=resource)
    ep = endpoint or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if ep:
        _provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=ep, insecure=True)))
    else:
        _provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(_provider)


def tracer():
    return trace.get_tracer(_TRACER_NAME)


@contextmanager
def rag_span(name: str, **attrs) -> Iterator[trace.Span]:
    """Convenience context manager for one span with attrs."""
    with tracer().start_as_current_span(name) as span:
        for k, v in attrs.items():
            if v is not None:
                span.set_attribute(k, v)
        yield span


@contextmanager
def llm_span(model: str, max_tokens: int, temperature: float) -> Iterator[trace.Span]:
    with tracer().start_as_current_span("gen_ai.invoke") as span:
        span.set_attribute("gen_ai.system", "aws.bedrock")
        span.set_attribute("gen_ai.request.model", model)
        span.set_attribute("gen_ai.request.max_tokens", max_tokens)
        span.set_attribute("gen_ai.request.temperature", temperature)
        yield span


def record_llm_result(
    span: trace.Span,
    input_tokens: int,
    output_tokens: int,
    finish_reason: str,
) -> None:
    span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
    span.set_attribute("gen_ai.usage.output_tokens", output_tokens)
    span.set_attribute("gen_ai.response.finish_reasons", [finish_reason])
