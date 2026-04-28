"""Content minimization shared by storage, export, and evidence sinks."""

from __future__ import annotations

import re
from typing import Any

from fisheye.preprocessors.redaction import PIIRedactionPreprocessor, SecretRedactionPreprocessor

_PATTERNS = (
    *SecretRedactionPreprocessor.patterns,
    *PIIRedactionPreprocessor.patterns,
    re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC )?PRIVATE KEY-----"),
)
_SENSITIVE_KEYS = re.compile(
    r"(?i)^(password|secret|api[_-]?key|access[_-]?token|authorization|cookie|private[_-]?key)$"
)


def redact(value: Any) -> Any:
    if isinstance(value, str):
        for pattern in _PATTERNS:
            value = pattern.sub("[REDACTED]", value)
        return value
    if isinstance(value, dict):
        return {redact(str(k)): "[REDACTED]" if _SENSITIVE_KEYS.match(str(k)) else redact(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [redact(v) for v in value]
    return value


def capture_event(event, raw=False, feature_only=False):
    """Extract local evidence before discarding sensitive content."""
    from fisheye.detectors.exfiltration import DataExfiltrationDetector
    from fisheye.detectors.prompt_injection import PromptInjectionDetector
    from fisheye.detectors.utils import extract_text
    from fisheye.schema.events import EventEnvelope

    text = extract_text(event.payload)
    analysis = {
        "sensitive_types": DataExfiltrationDetector()._scan_sensitive(text),
        "injection_rules": [name for name, pattern in PromptInjectionDetector().patterns if pattern.search(text)],
        "approx_tokens": max(len(text.split()), len(text) // 4),
        "content_bytes": len(text.encode()),
    }
    data = event.model_dump(mode="json")
    if not raw:
        data["payload"] = redact(data["payload"])
        data["meta"] = redact(data["meta"])
        data["tags"] = redact(data["tags"])
    data["meta"].pop("_route_mode", None)
    data["meta"]["_analysis"] = analysis
    data["meta"]["features"] = {"approx_tokens": analysis["approx_tokens"], "total_chars": len(text)}
    if feature_only:
        allowed = {
            "tool_name",
            "call_id",
            "recipient_id",
            "message_id",
            "source_event_ids",
            "artifact_ids",
            "artifact_id",
            "classification",
            "trust",
            "task_id",
            "parent_task_id",
            "allowed_tools",
            "deadline",
            "status",
            "waits_for",
            "required_artifacts",
            "verified",
            "requires_verification",
            "authorized",
            "tokens",
            "cost",
            "calls",
            "token_count",
            "latency_ms",
            "error_type",
            "action_id",
        }
        data["payload"] = {k: v for k, v in data["payload"].items() if k in allowed}
        data["meta"] = {"_analysis": analysis, "features": data["meta"]["features"], "feature_only": True}
        data["tags"] = {}
    return EventEnvelope.model_validate(data)
