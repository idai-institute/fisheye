from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


class EventType(str, Enum):
    AGENT_START = "agent.start"
    AGENT_STOP = "agent.stop"
    AGENT_ERROR = "agent.error"
    LLM_REQUEST = "llm.request"
    LLM_RESPONSE = "llm.response"
    LLM_MESSAGE = "llm.message"
    TOOL_CALL_START = "tool.call.start"
    TOOL_CALL_END = "tool.call.end"
    TOOL_CALL_ERROR = "tool.call.error"
    STATE_UPDATE = "state.update"
    NETWORK_REQUEST = "network.request"
    FILE_READ = "file.read"
    FILE_WRITE = "file.write"


class EventEnvelope(BaseModel):
    model_config = ConfigDict(extra="allow")

    event_id: str = Field(default_factory=lambda: uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    event_type: str
    agent_id: str
    run_id: str
    payload: dict[str, Any] = Field(default_factory=dict)

    trace_id: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None
    session_id: str | None = None
    framework: str | None = None
    tags: dict[str, str] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)

    @field_validator("timestamp", mode="before")
    @classmethod
    def _normalize_timestamp(cls, value: datetime | str) -> datetime:
        if isinstance(value, str):
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @field_validator("event_type")
    @classmethod
    def _validate_event_type(cls, value: str) -> str:
        if value.startswith("custom."):
            return value
        allowed = {item.value for item in EventType}
        if value not in allowed:
            raise ValueError(f"Unsupported event_type: {value}")
        return value
