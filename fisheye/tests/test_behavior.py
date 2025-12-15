from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from fisheye.behavior.monitor import StatisticalBehaviorMonitor
from fisheye.behavior.online_stats import EwmaTracker
from fisheye.collectors.sqlite_store import SQLiteStore
from fisheye.schema.events import EventEnvelope


def test_ewma_tracker_updates() -> None:
    tracker = EwmaTracker(alpha=0.5)
    assert tracker.update(10.0) == 0.0
    zscore = tracker.update(20.0)
    assert zscore != 0.0


def test_behavior_monitor_emits_alert() -> None:
    store = SQLiteStore(":memory:")
    monitor = StatisticalBehaviorMonitor(store=store, threshold=0.1, z_threshold=0.5)

    async def _run() -> None:
        base = datetime.now(timezone.utc)
        for i in range(6):
            event = EventEnvelope(
                event_type="tool.call.end",
                agent_id="agent",
                run_id="run",
                payload={"tool_name": "http", "latency_ms": 10 + i * 100},
                timestamp=base + timedelta(seconds=i),
            )
            await monitor.handle_event(event)

    asyncio.run(_run())
    alerts = asyncio.run(store.list_alerts(limit=50))
    assert any(item["category"] == "behavioral" for item in alerts)
    store.close()
