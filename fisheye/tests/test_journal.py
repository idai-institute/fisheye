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
