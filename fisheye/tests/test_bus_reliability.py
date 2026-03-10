import asyncio
import pytest
from fisheye.bus.async_bus import AsyncEventBus, OverloadedError
from fisheye.schema.events import EventEnvelope
from fisheye.tests.test_lifecycle import Recorder
from fisheye.preprocessors.buffering import BufferingPreprocessor
from fisheye.preprocessors.pipeline import PreprocessorPipeline
from fisheye.runtime import FisheyeRuntime


def test_reject_before_partial_fanout():
    async def run():
        bus = AsyncEventBus(queue_size=1)
        bus.subscribe(Recorder())
        event = EventEnvelope(event_type='agent.start', agent_id='a', run_id='r')
        await bus.publish(event)
        bus.subscribe(Recorder())
        with pytest.raises(OverloadedError):
            await bus.publish(event)
        assert bus.metrics['consumers']['2']['queued'] == 0
        assert bus.metrics['rejected'] == 1
    asyncio.run(run())


def test_idle_buffer_flushes_without_next_event():
    async def run():
        recorder = Recorder()
        runtime = FisheyeRuntime(AsyncEventBus(), preprocessor_pipeline=PreprocessorPipeline([
            BufferingPreprocessor(max_events=100, max_seconds=0.02)]))
        runtime.register_collector(recorder)
        async with runtime:
            await runtime.publish(dict(event_type='agent.start', agent_id='a', run_id='r'))
            await asyncio.sleep(0.15)
            await runtime.drain(1)
            assert len(recorder.events) == 1
    asyncio.run(run())
