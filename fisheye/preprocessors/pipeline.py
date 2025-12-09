from __future__ import annotations

from fisheye.preprocessors.base import Preprocessor
from fisheye.schema.events import EventEnvelope


class PreprocessorPipeline:
    def __init__(self, preprocessors: list[Preprocessor] | None = None) -> None:
        self.preprocessors = preprocessors or []

    async def process(self, event: EventEnvelope) -> list[EventEnvelope]:
        events: list[EventEnvelope] = [event]
        for preprocessor in self.preprocessors:
            next_events: list[EventEnvelope] = []
            for candidate in events:
                next_events.extend(await preprocessor.process(candidate))
            events = next_events
            if not events:
                break
        return events

    async def flush(self) -> list[EventEnvelope]:
        flushed: list[EventEnvelope] = []
        for index, preprocessor in enumerate(self.preprocessors):
            events = await preprocessor.flush()
            if not events:
                continue
            for downstream in self.preprocessors[index + 1 :]:
                next_events: list[EventEnvelope] = []
                for event in events:
                    next_events.extend(await downstream.process(event))
                events = next_events
                if not events:
                    break
            flushed.extend(events)
        return flushed
