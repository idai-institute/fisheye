import asyncio

import pytest

from fisheye.bus.async_bus import OverloadedError
from fisheye.collectors.journal import EventConflictError, JournalStore
from fisheye.schema.events import EventEnvelope


def test_durable_acceptance_conflicts_backlog_and_restart(tmp_path):
    async def run():
        path = tmp_path / "journal.db"
        store = JournalStore(path, max_pending=1)
        event = EventEnvelope(event_type="agent.start", agent_id="a", run_id="r")
        receipt = await store.accept(event)
        assert receipt.durable
        assert (await store.accept(event)).duplicate
        with pytest.raises(EventConflictError):
            await store.accept(event.model_copy(update={"payload": {"changed": True}}))
        with pytest.raises(OverloadedError):
            await store.accept(event.model_copy(update={"event_id": "other"}))
        store.close()
        store = JournalStore(path)
        sequence, restored = (await store.pending())[0]
        assert restored.event_id == event.event_id
        await store.commit_analysis(sequence, restored, {"version": 2})
        await store.commit_analysis(sequence, restored, {"version": 2})
        assert await store.pending() == []
        assert (await store.list_runs())[0]["event_count"] == 1
        assert await store.checkpoint(event.scope) == {"version": 2}
        store.close()

    asyncio.run(run())


def test_projection_failure_rolls_back_checkpoint_and_event(tmp_path, monkeypatch):
    async def run():
        store = JournalStore(tmp_path / "rollback.db")
        event = EventEnvelope(event_type="agent.start", agent_id="a", run_id="r")
        receipt = await store.accept(event)
        original = store._insert_event

        def broken(ev):
            original(ev)
            raise RuntimeError("fault")

        monkeypatch.setattr(store, "_insert_event", broken)
        with pytest.raises(RuntimeError):
            await store.commit_analysis(receipt.sequence, event, {})
        assert await store.list_runs() == []
        assert await store.checkpoint(event.scope) is None
        assert len(await store.pending()) == 1
        store.close()

    asyncio.run(run())


def test_redaction_cannot_hide_conflicting_secret_payload(tmp_path):
    from fisheye.runtime import build_default_runtime
    from fisheye.tests.test_privacy_and_auth import config

    async def run():
        async with build_default_runtime(config(tmp_path)) as runtime:
            event = EventEnvelope(
                event_type="llm.request", agent_id="a", run_id="r", payload={"password": "first-secret"}
            )
            await runtime.publish(event)
            assert (await runtime.publish(event)).duplicate
            changed = event.model_copy(update={"payload": {"password": "other-secret"}})
            with pytest.raises(EventConflictError):
                await runtime.publish(changed)

    asyncio.run(run())


def test_atomic_batches_and_concurrent_producer_connections(tmp_path):
    async def run():
        store = JournalStore(tmp_path / "batch.db")
        other = JournalStore(tmp_path / "batch.db")
        event = EventEnvelope(event_type="agent.start", agent_id="a", run_id="r")
        receipts = await asyncio.gather(store.accept(event), other.accept(event))
        assert sum(receipt.duplicate for receipt in receipts) == 1
        fresh = event.model_copy(update={"event_id": "new"})
        conflict = event.model_copy(update={"payload": {"conflict": True}})
        with pytest.raises(EventConflictError):
            await store.accept_many([fresh, conflict])
        assert len(await store.pending()) == 1
        await store.accept_many([fresh])
        items = [(seq, ev, {"last": ev.event_id}, (), (), (), ()) for seq, ev in await store.pending()]
        original = store._insert_event

        def fail_second(ev):
            original(ev)
            if ev.event_id == "new":
                raise RuntimeError("fault after first projection")

        store._insert_event = fail_second
        with pytest.raises(RuntimeError):
            await store.commit_analysis_batch(items)
        assert len(await store.pending()) == 2
        assert await store.list_runs() == []
        assert await store.pending_exports() == []
        store._insert_event = original
        await store.commit_analysis_batch(items)
        assert await store.checkpoint(event.scope) == {"last": "new"}
        assert (await store.list_runs())[0]["event_count"] == 2
        store.close()
        other.close()

    asyncio.run(run())
