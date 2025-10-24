from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field, field_validator

from fisheye.schema.events import EventEnvelope


class DetectorSignal(BaseModel):
    detector_id: str
    category: str
    score: float
    evidence: dict[str, Any] = Field(default_factory=dict)
    related_event_ids: list[str] = Field(default_factory=list)

    @field_validator("score")
    @classmethod
    def _score_range(cls, value: float) -> float:
        if value < 0.0 or value > 1.0:
            raise ValueError("signal score must be in [0.0, 1.0]")
        return value


class Detector(ABC):
    detector_id: str = "detector"
    supported_event_types: tuple[str, ...] | None = None

    @abstractmethod
    async def analyze(
        self,
        event: EventEnvelope,
        context: dict[str, Any],
    ) -> DetectorSignal | None:
        raise NotImplementedError
