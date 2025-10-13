from __future__ import annotations

from abc import ABC, abstractmethod

from fisheye.schema.events import EventEnvelope


class BehaviorMonitor(ABC):
    @abstractmethod
    async def observe(self, event: EventEnvelope) -> None:
        raise NotImplementedError
