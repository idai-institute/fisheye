"""Optional conversion from trace spans to versioned oversight events."""

from __future__ import annotations

from datetime import datetime, timezone

from fisheye.adapters.serialization import json_safe
from fisheye.schema.events import EventEnvelope


class OpenTelemetryAdapter:
    mapping_version = "genai-1"

    def __init__(self, runtime, application_id="default"):
        self.runtime, self.application_id = runtime, application_id

    async def record_span(self, span, workflow_id, agent_id):
        context = span.get_span_context()
        attrs = dict(span.attributes or {})
        operation = attrs.get("gen_ai.operation.name", "")
        tool = attrs.get("gen_ai.tool.name")
        kind = "tool.call.end" if operation == "execute_tool" or tool else "llm.response"
        payload = {
            "tool_name": tool,
            "operation": operation,
            "attributes": json_safe(attrs),
            "token_count": attrs.get("gen_ai.usage.input_tokens", 0) + attrs.get("gen_ai.usage.output_tokens", 0),
        }
        if span.start_time is not None and span.end_time is not None:
            payload["latency_ms"] = (span.end_time - span.start_time) / 1e6
        span_links = [
            {"trace_id": f"{link.context.trace_id:032x}", "span_id": f"{link.context.span_id:016x}"}
            for link in span.links
        ]
        return await self.runtime.publish(
            EventEnvelope(
                schema_version="2",
                application_id=self.application_id,
                event_id=f"otel-{context.trace_id:032x}-{context.span_id:016x}",
                event_type=kind,
                agent_id=agent_id,
                run_id=workflow_id,
                workflow_id=workflow_id,
                trace_id=f"{context.trace_id:032x}",
                span_id=f"{context.span_id:016x}",
                parent_span_id=f"{span.parent.span_id:016x}" if span.parent else None,
                timestamp=datetime.fromtimestamp(span.end_time / 1e9, timezone.utc)
                if span.end_time
                else datetime.now(timezone.utc),
                payload=payload,
                meta={"otel_mapping_version": self.mapping_version, "span_links": span_links},
            )
        )
