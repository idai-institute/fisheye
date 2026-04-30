from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from fisheye.schema.domain import Artifact, Delegation, Message, TaskStatus, Usage


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
    WORKFLOW_START = "workflow.start"
    WORKFLOW_STOP = "workflow.stop"
    TASK_DELEGATED = "task.delegated"
    TASK_STATUS = "task.status"
    MESSAGE_SENT = "message.sent"
    ARTIFACT_CREATED = "artifact.created"
    ARTIFACT_TRANSFERRED = "artifact.transferred"
    USAGE_RECORDED = "usage.recorded"
    ACTION_PROPOSED = "action.proposed"
    ACTION_COMPLETED = "action.completed"
    REVIEW_DECIDED = "review.decided"
    HEARTBEAT = "agent.heartbeat"


class EventEnvelope(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: Literal["1", "2"] = "1"
    application_id: str = Field(default="default", min_length=1, max_length=256)
    environment: str = Field(default="local", min_length=1, max_length=128)
    workflow_id: str | None = None
    agent_instance_id: str | None = None
    agent_role: str | None = None
    agent_version: str = "1"
    task_id: str | None = None
    producer_id: str | None = None
    producer_sequence: int | None = Field(default=None, ge=0)
    observed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    links: list[str] = Field(default_factory=list, max_length=100)
    event_id: str = Field(default_factory=lambda: uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    event_type: str
    agent_id: str = Field(min_length=1, max_length=256)
    run_id: str = Field(min_length=1, max_length=256)
    payload: dict[str, Any] = Field(default_factory=dict)

    trace_id: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None
    session_id: str | None = None
    framework: str | None = None
    tags: dict[str, str] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)

    @field_validator("timestamp", "observed_at", mode="before")
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

    @model_validator(mode="after")
    def _validate_payload(self) -> "EventEnvelope":
        models = {
            "task.delegated": Delegation,
            "message.sent": Message,
            "artifact.created": Artifact,
            "artifact.transferred": Artifact,
            "task.status": TaskStatus,
            "usage.recorded": Usage,
        }
        if self.schema_version == "2" and self.event_type in models:
            self.payload = models[self.event_type].model_validate(self.payload).model_dump(mode="json")
        try:
            json.dumps([self.payload, self.meta, self.tags], allow_nan=False)
            encoded = json.dumps(self.model_dump(mode="json"), allow_nan=False)
        except (ValueError, TypeError) as exc:
            raise ValueError("Event content must be finite JSON data") from exc
        if len(encoded.encode()) > 256 * 1024:
            raise ValueError("Event exceeds 256 KiB")
        return self

    @property
    def workflow(self) -> str:
        return self.workflow_id or self.run_id

    @property
    def scope(self) -> str:
        return json.dumps([self.application_id, self.environment, self.workflow], separators=(",", ":"))
