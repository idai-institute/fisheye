from __future__ import annotations

import asyncio
from typing import Any

from fisheye.runtime import FisheyeRuntime
from fisheye.schema.events import EventEnvelope


class SyncFisheyeRuntime:
    def __init__(self, runtime: FisheyeRuntime) -> None:
        self.runtime = runtime

    def start(self) -> None:
        asyncio.run(self.runtime.start())

    def stop(self) -> None:
        asyncio.run(self.runtime.stop())

    def publish(self, event: EventEnvelope | dict[str, Any]) -> None:
        asyncio.run(self.runtime.publish(event))

    def ingest(self, events: list[EventEnvelope | dict[str, Any]]) -> None:
        asyncio.run(self.runtime.ingest(events))

    def drain(self, timeout: float | None = None) -> None:
        asyncio.run(self.runtime.drain(timeout))
