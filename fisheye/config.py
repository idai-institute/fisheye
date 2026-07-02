from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True, allow_inf_nan=False)


class StorageConfig(ConfigModel):
    sqlite_path: Path = Path("./fisheye.db")
    events_jsonl_path: Path = Path("./fisheye-events.jsonl")
    alerts_jsonl_path: Path = Path("./fisheye-alerts.jsonl")
    max_pending: int = Field(default=100000, ge=1)
    poll_interval_seconds: float = Field(default=0.25, ge=0.01, le=60)
    retention_days: int = Field(default=30, ge=1)
    state_ttl_seconds: int = Field(default=86400, ge=1)
    capture: Literal["redacted", "raw", "features"] = "redacted"


class BusConfig(ConfigModel):
    queue_size: int = Field(default=1000, ge=1)
    retry_attempts: int = Field(default=1, ge=0, le=10)


class ApiConfig(ConfigModel):
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    api_key: str | None = Field(default=None, repr=False)
    review_api_key: str | None = Field(default=None, repr=False)
    application_id: str = "default"
    producer_id: str = "http"
    max_body_bytes: int = Field(default=2 * 1024 * 1024, ge=1024)


class ThresholdConfig(ConfigModel):
    prompt_injection: float = Field(default=0.7, ge=0, le=1)
    data_exfiltration: float = Field(default=0.7, ge=0, le=1)
    dos: float = Field(default=0.7, ge=0, le=1)
    behavioral: float = Field(default=0.75, ge=0, le=1)


class PreprocessorConfig(ConfigModel):
    enable_buffering: bool = False
    buffering_max_events: int = Field(default=20, ge=1)
    buffering_max_seconds: float = Field(default=2, gt=0)
    enable_secret_redaction: bool = True
    enable_pii_redaction: bool = True
    enable_hashing: bool = True
    enable_features: bool = True
    enable_url_extraction: bool = True
    enable_embeddings: bool = False
    embedding_provider: Literal["local_hash", "noop"] = "local_hash"
    embedding_dimensions: int = Field(default=64, ge=1, le=4096)


class OversightConfig(ConfigModel):
    graph_max_events: int = Field(default=2000, ge=10, le=100000)
    token_budget: int = Field(default=100000, ge=1)
    cost_budget: float = Field(default=100, gt=0)
    call_budget: int = Field(default=1000, ge=1)
    baseline_min_samples: int = Field(default=10, ge=2)
    baseline_frozen: bool = False


class FisheyeConfig(ConfigModel):
    storage: StorageConfig = Field(default_factory=StorageConfig)
    bus: BusConfig = Field(default_factory=BusConfig)
    api: ApiConfig = Field(default_factory=ApiConfig)
    thresholds: ThresholdConfig = Field(default_factory=ThresholdConfig)
    preprocessors: PreprocessorConfig = Field(default_factory=PreprocessorConfig)
    oversight: OversightConfig = Field(default_factory=OversightConfig)
    detector_weights: dict[str, float] = Field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "FisheyeConfig":
        return cls.model_validate(raw)

    @classmethod
    def load(cls, path: str | Path | None = None, overrides: dict | None = None, environ: dict | None = None):
        data = {}
        if path:
            path = Path(path)
            if path.suffix == ".json":
                data = json.loads(path.read_text())
            else:
                try:
                    import tomllib
                except ImportError:
                    import tomli as tomllib
                data = tomllib.loads(path.read_text())
        for key, value in (os.environ if environ is None else environ).items():
            if not key.startswith("FISHEYE__"):
                continue
            parts = key[len("FISHEYE__") :].lower().split("__")
            target = data
            for part in parts[:-1]:
                target = target.setdefault(part, {})
            try:
                value = json.loads(value)
            except (TypeError, json.JSONDecodeError):
                pass
            target[parts[-1]] = value

        def merge(target, extra):
            for key, value in extra.items():
                if isinstance(value, dict) and isinstance(target.get(key), dict):
                    merge(target[key], value)
                else:
                    target[key] = value

        merge(data, overrides or {})
        return cls.model_validate(data)

    def public_dict(self):
        data = self.model_dump(mode="json")
        for key in ("api_key", "review_api_key"):
            data["api"][key] = "[REDACTED]" if data["api"][key] else None
        return data

    @property
    def fingerprint(self):
        return hashlib.sha256(json.dumps(self.public_dict(), sort_keys=True).encode()).hexdigest()[:16]
