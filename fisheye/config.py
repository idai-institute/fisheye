from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class StorageConfig:
    sqlite_path: Path = Path("./fisheye.db")
    events_jsonl_path: Path = Path("./fisheye-events.jsonl")
    alerts_jsonl_path: Path = Path("./fisheye-alerts.jsonl")


@dataclass(slots=True)
class BusConfig:
    queue_size: int = 1000
    retry_attempts: int = 1


@dataclass(slots=True)
class ApiConfig:
    host: str = "127.0.0.1"
    port: int = 8000
    api_key: str | None = None


@dataclass(slots=True)
class ThresholdConfig:
    prompt_injection: float = 0.7
    data_exfiltration: float = 0.7
    dos: float = 0.7
    behavioral: float = 0.75


@dataclass(slots=True)
class PreprocessorConfig:
    enable_buffering: bool = False
    buffering_max_events: int = 20
    buffering_max_seconds: float = 2.0
    enable_secret_redaction: bool = False
    enable_pii_redaction: bool = False
    enable_hashing: bool = True
    enable_features: bool = False
    enable_url_extraction: bool = False


@dataclass(slots=True)
class FisheyeConfig:
    storage: StorageConfig = field(default_factory=StorageConfig)
    bus: BusConfig = field(default_factory=BusConfig)
    api: ApiConfig = field(default_factory=ApiConfig)
    thresholds: ThresholdConfig = field(default_factory=ThresholdConfig)
    preprocessors: PreprocessorConfig = field(default_factory=PreprocessorConfig)
    detector_weights: dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "FisheyeConfig":
        # Minimal explicit parser to keep config ergonomics simple in MVP.
        cfg = cls()
        if storage := raw.get("storage"):
            cfg.storage = StorageConfig(**storage)
        if bus := raw.get("bus"):
            cfg.bus = BusConfig(**bus)
        if api := raw.get("api"):
            cfg.api = ApiConfig(**api)
        if thresholds := raw.get("thresholds"):
            cfg.thresholds = ThresholdConfig(**thresholds)
        if preprocessors := raw.get("preprocessors"):
            cfg.preprocessors = PreprocessorConfig(**preprocessors)
        if detector_weights := raw.get("detector_weights"):
            cfg.detector_weights = dict(detector_weights)
        return cfg
