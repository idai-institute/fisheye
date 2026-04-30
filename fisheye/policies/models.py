from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    action_id: str = Field(default_factory=lambda: uuid4().hex)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    application_id: str = "default"
    environment: str = "local"
    workflow_id: str
    agent_id: str
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    destination: str | None = None
    classification: Literal["public", "internal", "confidential", "secret"] = "public"
    delegation_depth: int = Field(default=0, ge=0)
    estimated_cost: float = Field(default=0, ge=0, allow_inf_nan=False)
    estimated_tokens: int = Field(default=0, ge=0)
    source_event_ids: list[str] = Field(default_factory=list)

    @field_validator("arguments")
    @classmethod
    def json_arguments(cls, value):
        encoded = json.dumps(value, allow_nan=False)
        if len(encoded.encode()) > 65536:
            raise ValueError("Action arguments exceed 64 KiB")
        return value

    @property
    def digest(self):
        return hashlib.sha256(
            json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    @property
    def scope(self):
        return json.dumps([self.application_id, self.environment, self.workflow_id], separators=(",", ":"))


class Policy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = "default"
    allowed_tools: set[str] | None = None
    denied_tools: set[str] = Field(default_factory=set)
    review_tools: set[str] = Field(default_factory=set)
    allowed_destinations: set[str] | None = None
    deny_sensitive_egress: bool = True
    max_delegation_depth: int = Field(default=5, ge=0)
    max_cost: float = Field(default=100, gt=0, allow_inf_nan=False)
    max_tokens: int = Field(default=100000, gt=0)
    max_calls: int = Field(default=1000, gt=0)
    approval_ttl_seconds: int = Field(default=300, ge=1, le=86400)
    reviewers: set[str] = Field(default_factory=lambda: {"operator"})
    blocked_finding_categories: set[str] = Field(default_factory=set)

    @property
    def version(self):
        data = self.model_dump(mode="json")
        for key, value in data.items():
            if isinstance(value, list):
                data[key] = sorted(value)
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:20]

    def evaluate(self, action: Action, findings=()):
        if action.tool_name in self.denied_tools or (
            self.allowed_tools is not None and action.tool_name not in self.allowed_tools
        ):
            return "deny", "tool_not_allowed"
        if action.delegation_depth > self.max_delegation_depth:
            return "deny", "delegation_depth"
        if action.destination:
            target = urlsplit(action.destination)
            if target.scheme not in {"https", "http"} or not target.hostname or target.username or target.password:
                return "deny", "invalid_destination"
            host = target.hostname.rstrip(".").encode("idna").decode().lower()
            if self.allowed_destinations is not None and host not in {
                h.rstrip(".").encode("idna").decode().lower() for h in self.allowed_destinations
            }:
                return "deny", "destination_not_allowed"
            if self.deny_sensitive_egress and action.classification in {"confidential", "secret"}:
                return "deny", "sensitive_egress"
        if any(
            f["category"] in self.blocked_finding_categories and f["status"] in {"open", "acknowledged"}
            for f in findings
        ):
            return "deny", "active_finding"
        if action.tool_name in self.review_tools:
            return "require_review", "human_review"
        return "allow", "policy_allowed"


class Decision(BaseModel):
    action_id: str
    decision: Literal["allow", "deny", "require_review"]
    reason: str
    policy_version: str
    action_digest: str
    status: str
    expires_at: datetime
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ActionDenied(RuntimeError):
    pass


class ReviewRequired(ActionDenied):
    pass
