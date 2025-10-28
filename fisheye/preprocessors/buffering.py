from __future__ import annotations

import time

from fisheye.preprocessors.base import Preprocessor
from fisheye.schema.events import EventEnvelope


class BufferingPreprocessor(Preprocessor):
    def __init__(self, max_events: int = 20, max_seconds: float = 2.0) -> None:
        self.max_events = max_events
        self.max_seconds = max_seconds
        self._buffer: list[EventEnvelope] = []
        self._last_flush = time.monotonic()

    def _should_flush(self) -> bool:
        if len(self._buffer) >= self.max_events:
            return True
        return (time.monotonic() - self._last_flush) >= self.max_seconds

    async def process(self, event: EventEnvelope) -> list[EventEnvelope]:
        self._buffer.append(event)
        if self._should_flush():
            return await self.flush()
        return []

    async def flush(self) -> list[EventEnvelope]:
        out = self._buffer
        self._buffer = []
        self._last_flush = time.monotonic()
        return out
