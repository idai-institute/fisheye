from __future__ import annotations

import asyncio

from fisheye.config import FisheyeConfig
from fisheye.runtime import build_default_runtime


def test_multi_agent_runs_are_queryable(tmp_path) -> None:
    cfg = FisheyeConfig()
    cfg.storage.sqlite_path = tmp_path / "multi.db"
    cfg.storage.events_jsonl_path = tmp_path / "events.jsonl"
    cfg.storage.alerts_jsonl_path = tmp_path / "alerts.jsonl"

    runtime = build_default_runtime(cfg)

    async def _run() -> tuple[list[dict], list[dict], list[dict]]:
        await runtime.start()
        await runtime.ingest(
            [
                {
                    "event_type": "agent.start",
                    "agent_id": "a1",
                    "run_id": "r1",
                    "payload": {},
                },
                {
                    "event_type": "agent.start",
                    "agent_id": "a2",
                    "run_id": "r2",
                    "payload": {},
                },
                {
                    "event_type": "llm.request",
                    "agent_id": "a1",
                    "run_id": "r1",
                    "payload": {"message": "hello"},
                },
                {
                    "event_type": "llm.request",
                    "agent_id": "a2",
                    "run_id": "r2",
                    "payload": {"message": "world"},
                },
            ]
        )
        await runtime.drain(timeout=3.0)
        runs = await runtime.store.list_runs(limit=10)
        events_r1 = await runtime.store.get_run_events("r1", limit=10)
        events_r2 = await runtime.store.get_run_events("r2", limit=10)
        await runtime.stop()
        runtime.store.close()
        return runs, events_r1, events_r2

    runs, events_r1, events_r2 = asyncio.run(_run())
    run_ids = {row["run_id"] for row in runs}
    assert {"r1", "r2"}.issubset(run_ids)
    assert all(event["run_id"] == "r1" for event in events_r1)
    assert all(event["run_id"] == "r2" for event in events_r2)
