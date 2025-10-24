from __future__ import annotations

from fisheye.schema.events import EventEnvelope


class Preprocessor:
    async def process(self, event: EventEnvelope) -> list[EventEnvelope]:
        return [event]

    async def flush(self) -> list[EventEnvelope]:
        return []
