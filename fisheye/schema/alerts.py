from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

AlertCategory = str


class Alert(BaseModel):
    alert_id: str = Field(default_factory=lambda: uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    agent_id: str
    run_id: str
    category: AlertCategory
    score: float = Field(allow_inf_nan=False)
    threshold: float = Field(allow_inf_nan=False)
    triggered: bool
    sources: list[str] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)
    related_event_ids: list[str] = Field(default_factory=list)

    @field_validator("score", "threshold")
    @classmethod
    def _validate_score(cls, value: float) -> float:
        if value < 0.0 or value > 1.0:
            raise ValueError("score/threshold must be in [0.0, 1.0]")
        return value
