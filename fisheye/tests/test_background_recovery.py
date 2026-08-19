import asyncio
import threading

import pytest

from fisheye.collectors.jsonl_logger import JsonlLoggerCollector
from fisheye.runtime import build_default_runtime
from fisheye.schema.events import EventEnvelope
from fisheye.tests.test_privacy_and_auth import config


async def eventually(predicate):
    async def check():
        while not predicate():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(check(), 3)


def test_retention_failure_is_visible_and_periodic_worker_recovers(tmp_path):
    async def run():
        async with build_default_runtime(config(tmp_path)) as runtime:
            original = runtime.store.prune

            async def fail(*args):
                raise OSError("maintenance unavailable")

            runtime.store.prune = fail
            runtime._last_maintenance -= 61
            await eventually(lambda: runtime.metrics["maintenance_error"] == "OSError")
            assert not runtime._flush_task.done()
            await runtime.publish(dict(event_type="agent.start", agent_id="a", run_id="r"))
            await runtime.drain(3)
            runtime.store.prune = original
            runtime._last_maintenance -= 61
            await eventually(lambda: runtime.metrics["maintenance_error"] is None)
            assert not runtime._flush_task.done()

    asyncio.run(run())


def test_periodic_flush_failure_does_not_disable_other_routes(tmp_path):
    async def run():
        cfg = config(tmp_path)
        cfg.preprocessors.enable_buffering = True
        cfg.preprocessors.buffering_max_seconds = 0.01
        async with build_default_runtime(cfg) as runtime:
            flushed = []

            async def flush(mode):
                if mode == "raw":
                    raise ValueError("raw route failed")
                flushed.append(mode)

            original = runtime._flush_mode
            runtime._flush_mode = flush
            await eventually(lambda: "redacted" in flushed and "feature_only" in flushed)
            assert runtime.metrics["projection_errors"] >= 1
            assert runtime.metrics["projection_last_error"] == "ValueError"
            assert not runtime._flush_task.done()
            runtime._flush_mode = original

    asyncio.run(run())


def test_cancelled_jsonl_write_holds_lock_until_thread_finishes(tmp_path):
    async def run():
        path = tmp_path / "events.jsonl"
        logger = JsonlLoggerCollector(path)
        entered, release = threading.Event(), threading.Event()
        original = logger._append_line

        def blocked(target, line):
            entered.set()
            assert release.wait(3)
            original(target, line)

        logger._append_line = blocked
        event = EventEnvelope(event_type="agent.start", agent_id="a", run_id="r")
        task = asyncio.create_task(logger.handle_event(event))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            for _ in range(2):
                task.cancel()
                await asyncio.sleep(0.01)
                assert not task.done() and logger._lock.locked()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not logger._lock.locked()
        assert len(path.read_text().splitlines()) == 1

    asyncio.run(run())
