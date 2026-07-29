import asyncio
import subprocess
import sys
import threading

import pytest

from fisheye.collectors.journal import JournalStore
from fisheye.runtime import build_default_runtime
from fisheye.schema.events import EventEnvelope
from fisheye.state.lease import AnalysisLease
from fisheye.tests.test_privacy_and_auth import config


def test_only_one_analyzer_and_restart_releases_ownership(tmp_path):
    async def run():
        first, second = [build_default_runtime(config(tmp_path)) for _ in range(2)]
        await first.start()
        with pytest.raises(RuntimeError, match="Another analysis runtime"):
            await second.start()
        assert not second._started and not second.bus._running
        await first.aclose()
        await second.start()
        await second.aclose()
        await second.aclose()
        with pytest.raises(RuntimeError, match="closed"):
            await second.start()

    asyncio.run(run())


def test_lease_is_process_shared_and_released_on_abrupt_exit(tmp_path):
    path = tmp_path / "events.db"
    lease = AnalysisLease(path)
    lease.acquire()
    child = """
import os, sys
from fisheye.state.lease import AnalysisLease
lease = AnalysisLease(sys.argv[1])
try:
    lease.acquire()
except RuntimeError:
    sys.exit(7)
os._exit(23)
"""
    try:
        assert subprocess.run([sys.executable, "-c", child, str(path)], timeout=5).returncode == 7
    finally:
        lease.release()
    assert subprocess.run([sys.executable, "-c", child, str(path)], timeout=5).returncode == 23
    lease.acquire()
    lease.release()


def test_external_producer_is_processed_without_local_publish(tmp_path):
    async def run():
        async with build_default_runtime(config(tmp_path)) as runtime:
            await runtime.drain(2)
            producer = JournalStore(runtime.store.db_path)
            try:
                event = EventEnvelope(event_type="agent.start", agent_id="external", run_id="r")
                await producer.accept(event)

                async def processed():
                    while (await producer.journal_metrics())["pending"]:
                        await asyncio.sleep(0.02)

                await asyncio.wait_for(processed(), 3)
                assert (await producer.list_runs())[0]["event_count"] == 1
            finally:
                producer.close()

    asyncio.run(run())


def test_start_failure_does_not_leave_workers_or_lease(tmp_path, monkeypatch):
    async def run():
        runtime = build_default_runtime(config(tmp_path))

        async def fail(*args):
            raise OSError("storage unavailable")

        monkeypatch.setattr(runtime.store, "prune", fail)
        with pytest.raises(OSError):
            await runtime.start()
        assert not runtime._started and not runtime.bus._running
        lease = AnalysisLease(runtime.store.db_path)
        lease.acquire()
        lease.release()
        await runtime.aclose()

    asyncio.run(run())


def test_cancelled_commit_waits_for_sqlite_transaction(tmp_path, monkeypatch):
    async def run():
        store = JournalStore(tmp_path / "events.db")
        event = EventEnvelope(event_type="agent.start", agent_id="a", run_id="r")
        receipt = await store.accept(event)
        entered, release = threading.Event(), threading.Event()
        original = store._insert_event

        def blocked(ev):
            original(ev)
            entered.set()
            assert release.wait(5)

        monkeypatch.setattr(store, "_insert_event", blocked)
        task = asyncio.create_task(store.commit_analysis(receipt.sequence, event, {}))
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        await asyncio.sleep(0.02)
        assert not task.done()
        task.cancel()
        await asyncio.sleep(0.02)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not await store.pending()
        store.close()

    asyncio.run(run())
