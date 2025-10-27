from __future__ import annotations

from abc import ABC, abstractmethod

from fisheye.schema.alerts import Alert
from fisheye.schema.events import EventEnvelope


class Collector(ABC):
    name: str = "collector"

    @abstractmethod
    async def handle_event(self, event: EventEnvelope) -> None:
        raise NotImplementedError


class AlertSink(ABC):
    @abstractmethod
    async def handle_alert(self, alert: Alert) -> None:
        raise NotImplementedError
