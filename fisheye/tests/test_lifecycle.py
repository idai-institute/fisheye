import asyncio

import pytest

from fisheye.adapters.langchain import LangChainAdapter
from fisheye.bus.async_bus import AsyncEventBus
from fisheye.collectors.base import Collector
from fisheye.runtime import FisheyeRuntime
from fisheye.sync.wrappers import SyncFisheyeRuntime


class Recorder(Collector):
    name = "recorder"

    def __init__(self):
        self.events = []

    async def handle_event(self, event):
        self.events.append(event)


def test_sync_lifecycle_delivers_and_joins_worker():
    runtime = FisheyeRuntime(AsyncEventBus())
    recorder = Recorder()
    runtime.register_collector(recorder)
    with SyncFisheyeRuntime(runtime) as sync:
        for i in range(20):
            sync.publish(dict(event_type="agent.start", agent_id="a", run_id=str(i)))
        sync.drain(timeout=2)
    assert len(recorder.events) == 20
    assert sync._thread is None


def test_sync_callbacks_use_persistent_runtime():
    runtime = FisheyeRuntime(AsyncEventBus())
    recorder = Recorder()
    runtime.register_collector(recorder)
    handler = LangChainAdapter(runtime, "a", "r").as_callback_handler()
    handler.on_llm_start({}, ["hello"])
    handler.on_llm_end("world")
    runtime.close()
    assert [e.event_type for e in recorder.events] == ["llm.request", "llm.response"]


def test_async_drain_awaits_callback_submissions():
    async def run():
        runtime = FisheyeRuntime(AsyncEventBus())
        recorder = Recorder()
        runtime.register_collector(recorder)
        async with runtime:
            adapter = LangChainAdapter(runtime, "a", "r")
            adapter.as_callback_handler().on_llm_start({}, ["hello"])
            await runtime.drain(2)
            assert len(recorder.events) == 1

    asyncio.run(run())


def test_foreign_thread_callback_errors_surface_at_drain():
    async def run():
        runtime = FisheyeRuntime(AsyncEventBus())
        async with runtime:

            async def unavailable(event):
                raise ValueError("acceptance unavailable")

            runtime.publish = unavailable
            handler = LangChainAdapter(runtime, "a", "r").as_callback_handler()
            await asyncio.to_thread(handler.on_llm_start, {}, ["hello"])
            await asyncio.sleep(0.01)
            with pytest.raises(RuntimeError, match="Callback delivery failed"):
                await runtime.drain(2)

    asyncio.run(run())
