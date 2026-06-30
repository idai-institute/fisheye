import asyncio
import time

import pytest

from fisheye.bus.async_bus import AsyncEventBus
from fisheye.runtime import FisheyeRuntime, build_default_runtime
from fisheye.tests.test_lifecycle import Recorder
from fisheye.tests.test_privacy_and_auth import config


def test_drain_waits_for_post_commit_consumer_projection(tmp_path):
    async def run():
        async with build_default_runtime(config(tmp_path)) as runtime:
            recorder = Recorder()
            runtime.register_collector(recorder, mode="raw")
            entered, release = asyncio.Event(), asyncio.Event()
            pipeline = runtime.preprocessor_pipelines["raw"]
            original = pipeline.process

            async def delayed(event):
                entered.set()
                await release.wait()
                return await original(event)

            pipeline.process = delayed
            await runtime.publish(dict(event_type="agent.start", agent_id="a", run_id="r"))
            await asyncio.wait_for(entered.wait(), 2)
            waiter = asyncio.create_task(runtime.drain(2))
            await asyncio.sleep(0.02)
            assert not waiter.done()
            release.set()
            await waiter
            assert len(recorder.events) == 1

    asyncio.run(run())


def test_drain_timeout_does_not_cancel_callback():
    async def run():
        async with FisheyeRuntime(AsyncEventBus()) as runtime:
            release = asyncio.Event()
            completed = []

            async def callback():
                await release.wait()
                completed.append(True)

            task = runtime.submit(callback())
            with pytest.raises(asyncio.TimeoutError):
                await runtime.drain(0.01)
            assert not task.done()
            release.set()
            await runtime.drain(1)
            assert completed == [True]

    asyncio.run(run())


def test_drain_deadline_covers_all_stages(tmp_path, monkeypatch):
    async def run():
        async with build_default_runtime(config(tmp_path)) as runtime:
            await runtime.drain(2)

            async def metrics():
                await asyncio.sleep(0.04)
                return {"pending": 0}

            async def exports(*args):
                await asyncio.sleep(0.04)
                return []

            with monkeypatch.context() as patch:
                patch.setattr(runtime.store, "journal_metrics", metrics)
                patch.setattr(runtime.store, "pending_exports", exports)
                start = time.monotonic()
                with pytest.raises(asyncio.TimeoutError):
                    await runtime.drain(0.06)
                assert time.monotonic() - start < 0.15

    asyncio.run(run())


def test_failed_projection_does_not_skip_other_routes_or_events(tmp_path):
    async def run():
        async with build_default_runtime(config(tmp_path)) as runtime:
            broken, healthy = Recorder(), Recorder()
            runtime.register_collector(broken, mode="raw")
            runtime.register_collector(healthy, mode="redacted")

            async def fail(event):
                raise ValueError("bad projection")

            runtime.preprocessor_pipelines["raw"].process = fail
            await runtime.ingest([dict(event_type="agent.start", agent_id="a", run_id=str(i)) for i in range(3)])
            await runtime.drain(3)
            assert len(healthy.events) == 3 and not broken.events
            assert runtime.metrics["projection_errors"] == 3
            assert runtime.metrics["analysis_error"] is None

    asyncio.run(run())


def test_stop_cleans_up_workers_when_flush_fails():
    async def run():
        runtime = FisheyeRuntime(AsyncEventBus())
        runtime.register_collector(Recorder())
        await runtime.start()

        async def fail():
            raise ValueError("flush failed")

        runtime.preprocessor_pipelines["raw"].flush = fail
        with pytest.raises(ValueError, match="flush failed"):
            await runtime.stop()
        assert not runtime._started and not runtime.bus._running
        assert runtime.bus._workers == {}

    asyncio.run(run())
