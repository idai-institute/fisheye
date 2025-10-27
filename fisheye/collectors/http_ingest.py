from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from fisheye.runtime import FisheyeRuntime
from fisheye.schema.events import EventEnvelope


class HttpIngestService:
    """Adapter used by the REST API layer to publish ingress events into runtime."""

    def __init__(self, runtime: FisheyeRuntime) -> None:
        self.runtime = runtime

    async def ingest(self, events: Sequence[EventEnvelope | dict[str, Any]]) -> int:
        count = 0
        for event in events:
            await self.runtime.publish(event)
            count += 1
        return count
