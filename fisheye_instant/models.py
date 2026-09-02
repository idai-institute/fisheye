from __future__ import annotations

import math
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    id: str = Field(default_factory=lambda: uuid4().hex, min_length=1, max_length=64, pattern=r"^[\w-]+$")
    name: str = Field(min_length=1, max_length=80)
    action: Literal["warning", "email", "shutdown"] = "warning"
    threshold: float = Field(default=60, ge=1, le=100)
    enabled: bool = True
    cooldown_seconds: int = Field(default=300, ge=0, le=86400)
    hysteresis: float = Field(default=10, ge=1, le=50)
    workflow_id: str | None = Field(default=None, max_length=256)
    environment: str | None = Field(default=None, max_length=128)
    recipients: tuple[str, ...] = ()

    @field_validator("recipients")
    @classmethod
    def addresses(cls, values):
        if len(values) > 10:
            raise ValueError("Use at most ten recipients")
        for value in values:
            if len(value) > 254 or value.count("@") != 1 or any(c.isspace() or ord(c) < 32 for c in value):
                raise ValueError("Enter email addresses without display names")
            if not all(value.split("@")) or any(c in value for c in "<>,;\\"):
                raise ValueError("Invalid email address")
        return tuple(dict.fromkeys(values))

    @model_validator(mode="after")
    def valid_rule(self):
        if self.action == "email" and not self.recipients:
            raise ValueError("Email rules need at least one recipient")
        if self.hysteresis >= self.threshold:
            raise ValueError("Rearm margin must be lower than the threshold")
        return self


class MailSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    host: str = Field(default="", max_length=253)
    port: int = Field(default=587, ge=1, le=65535)
    security: Literal["starttls", "tls"] = "starttls"
    username: str = Field(default="", max_length=254)
    sender: str = ""

    @field_validator("host", "username", "sender")
    @classmethod
    def no_controls(cls, value):
        if any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("Control characters are not allowed")
        return value.strip()

    @model_validator(mode="after")
    def sender_address(self):
        if self.sender:
            Rule.addresses((self.sender,))
        if self.host and not self.sender:
            raise ValueError("Provide a sender address")
        return self


def anomaly_score(channels):
    """A 0–100 heuristic: noisy-OR of each channel's strongest recent signal.

    A channel is a detector, a behavior source, or a graph finding category.
    Duplicate observations do not accumulate; this is not a probability.
    """
    peaks = {}
    for item in channels:
        value = float(item["score"])
        if not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError("Signal scores must be finite and in [0, 1]")
        key = item["channel"]
        if key not in peaks or value > peaks[key]["score"]:
            peaks[key] = dict(item, score=value)
    residual = math.prod(1 - row["score"] for row in peaks.values())
    return round(100 * (1 - residual), 1), sorted(peaks.values(), key=lambda r: (-r["score"], r["channel"]))
