"""Portable contracts for workflow relationships and policy evidence."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Delegation(Payload):
    task_id: str = Field(min_length=1, max_length=256)
    recipient_id: str = Field(min_length=1, max_length=256)
    parent_task_id: str | None = None
    allowed_tools: list[str] = Field(default_factory=list)
    source_event_ids: list[str] = Field(default_factory=list)
    deadline: datetime | None = None

    @field_validator("deadline")
    @classmethod
    def utc_deadline(cls, value):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class Message(Payload):
    message_id: str = Field(default_factory=lambda: uuid4().hex)
    recipient_id: str
    content: str = ""
    source_event_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    trust: Literal["trusted", "untrusted", "unknown", "quoted"] = "unknown"


class Artifact(Payload):
    artifact_id: str
    classification: Literal["public", "internal", "confidential", "secret"] = "public"
    source_event_ids: list[str] = Field(default_factory=list)
    content: str | None = None
    destination: str | None = None
    authorized: bool = False


class TaskStatus(Payload):
    task_id: str
    status: Literal["started", "waiting", "completed", "failed", "cancelled"]
    waits_for: list[str] = Field(default_factory=list)
    required_artifacts: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    verified: bool = False
    requires_verification: bool = False


class Usage(Payload):
    tokens: int = Field(default=0, ge=0)
    cost: float = Field(default=0, ge=0, allow_inf_nan=False)
    calls: int = Field(default=0, ge=0)


class Finding(BaseModel):
    finding_id: str
    application_id: str = "default"
    environment: str = "local"
    workflow_id: str
    category: str
    severity: Literal["info", "low", "medium", "high", "critical"] = "medium"
    score: float = Field(ge=0, le=1, allow_inf_nan=False)
    score_kind: Literal["heuristic", "calibrated"] = "heuristic"
    title: str
    agent_ids: list[str] = Field(default_factory=list)
    event_ids: list[str] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)
    evidence_truncated: bool = False
    status: Literal["open", "acknowledged", "resolved", "false_positive"] = "open"
    detector_version: str = "2.0"
    config_version: str = ""
    first_seen: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_seen: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    occurrences: int = 1


class PluginSpec(BaseModel):
    plugin_id: str
    version: str = "1"
    schema_versions: tuple[str, ...] = ("1", "2")
    event_types: tuple[str, ...] = ()
    scope: Literal["agent", "workflow"] = "workflow"
    requires_content: bool = False
    timeout_seconds: float = Field(default=1, gt=0)
    state_ttl_seconds: int = Field(default=86400, gt=0)
    max_state_bytes: int = Field(default=262144, gt=0)
    configuration: dict[str, Any] = Field(default_factory=dict)
