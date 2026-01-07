from __future__ import annotations

import asyncio

from fisheye.adapters.camel import CamelAdapter
from fisheye.adapters.langchain import LangChainAdapter
from fisheye.adapters.openai_agents import OpenAIAgentsAdapter
from fisheye.bus.async_bus import AsyncEventBus
from fisheye.collectors.base import Collector
from fisheye.preprocessors.pipeline import PreprocessorPipeline
from fisheye.runtime import FisheyeRuntime


class _RecordingCollector(Collector):
    def __init__(self) -> None:
        self.events = []
        self.name = "recording"

    async def handle_event(self, event):
        self.events.append(event)


class _DummyRuntime:
    def __init__(self) -> None:
        self.events = []

    async def publish(self, event):
        self.events.append(event)


def test_routing_modes() -> None:
    raw_collector = _RecordingCollector()
    redacted_collector = _RecordingCollector()

    runtime = FisheyeRuntime(
        bus=AsyncEventBus(),
        preprocessor_pipelines={
            "raw": PreprocessorPipeline([]),
            "redacted": PreprocessorPipeline([]),
        },
    )

    async def _run() -> tuple[list, list]:
        runtime.register_collector(raw_collector, mode="raw")
        runtime.register_collector(redacted_collector, mode="redacted")
        await runtime.start()
        await runtime.publish(
            {
                "event_type": "llm.request",
                "agent_id": "agent",
                "run_id": "run",
                "payload": {"message": "keep this raw"},
            }
        )
        await runtime.drain(timeout=2.0)
        await runtime.stop()
        return raw_collector.events, redacted_collector.events

    raw_events, redacted_events = asyncio.run(_run())
    assert raw_events
    assert redacted_events
    assert raw_events[0].payload["message"] == "keep this raw"
    assert redacted_events[0].payload["message"] == "keep this raw"


def test_langchain_callback_handler_dispatches_events() -> None:
    runtime = _DummyRuntime()
    adapter = LangChainAdapter(runtime=runtime, agent_id="agent", run_id="run")
    handler = adapter.as_callback_handler()

    handler.on_llm_start({"name": "model"}, ["hello from prompt"], run_id="abc")
    handler.on_tool_start({"name": "search"}, "query")

    assert len(runtime.events) == 2
    assert runtime.events[0].event_type == "llm.request"
    assert runtime.events[1].event_type == "tool.call.start"


def test_camel_and_openai_adapter_wrappers_emit() -> None:
    runtime = _DummyRuntime()
    camel = CamelAdapter(runtime=runtime, agent_id="agent", run_id="run")
    openai = OpenAIAgentsAdapter(runtime=runtime, agent_id="agent", run_id="run")

    async def add(a: int, b: int) -> int:
        return a + b

    async def _run() -> None:
        wrapped_camel = camel.wrap_tool("sum", add)
        wrapped_openai = openai.wrap_tool("sum", add)
        assert await wrapped_camel(1, 2) == 3
        assert await wrapped_openai(2, 3) == 5
        hook = openai.as_event_hook()
        hook.on_event({"type": "run.started", "payload": {"source": "test"}})

    asyncio.run(_run())

    event_types = [event.event_type for event in runtime.events]
    assert "tool.call.start" in event_types
    assert "tool.call.end" in event_types
    assert "agent.start" in event_types
