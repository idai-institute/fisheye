from __future__ import annotations

import asyncio

from fisheye.collectors.sqlite_store import SQLiteStore
from fisheye.schema.alerts import Alert
from fisheye.schema.events import EventEnvelope


def test_sqlite_store_round_trip(tmp_path) -> None:
    db = tmp_path / "store.db"
    store = SQLiteStore(db)

    event = EventEnvelope(
        event_type="llm.request",
        agent_id="agent",
        run_id="run-1",
        payload={"message": "hello"},
    )

    alert = Alert(
        agent_id="agent",
        run_id="run-1",
        category="prompt_injection",
        score=0.8,
        threshold=0.7,
        triggered=True,
        evidence={"reason": "test"},
    )

    async def _run() -> None:
        await store.handle_event(event)
        await store.handle_alert(alert)

    asyncio.run(_run())

    alerts = asyncio.run(store.list_alerts(limit=10))
    runs = asyncio.run(store.list_runs(limit=10))
    events = asyncio.run(store.get_run_events("run-1", limit=10))

    assert len(alerts) == 1
    assert len(runs) == 1
    assert len(events) == 1
    store.close()
