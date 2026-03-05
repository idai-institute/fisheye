from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

from fisheye.detectors.base import Detector, DetectorSignal
from fisheye.detectors.utils import clamp, extract_text, shannon_entropy
from fisheye.schema.events import EventEnvelope

SENSITIVE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("api_key", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("password", re.compile(r"(?i)password\s*[:=]\s*\S+")),
    ("token", re.compile(r"(?i)(access|refresh)?_?token\s*[:=]\s*[A-Za-z0-9_\-]{8,}")),
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----")),
)


class DataExfiltrationDetector(Detector):
    detector_id = "data_exfiltration_rules"
    supported_event_types = (
        "llm.request",
        "llm.response",
        "llm.message",
        "network.request",
        "file.write",
        "tool.call.end",
    )

    def __init__(self, sensitive_window_seconds: int = 300, outbound_size_threshold: int = 512) -> None:
        self.sensitive_window = timedelta(seconds=sensitive_window_seconds)
        self.outbound_size_threshold = outbound_size_threshold

    def _scan_sensitive(self, text: str) -> list[str]:
        hits: list[str] = []
        for label, pattern in SENSITIVE_PATTERNS:
            if pattern.search(text):
                hits.append(label)
        if len(text) >= 20 and shannon_entropy(text) > 4.3:
            hits.append("high_entropy")
        return sorted(set(hits))

    def _is_outbound_event(self, event: EventEnvelope) -> bool:
        if event.event_type in {"network.request", "file.write"}:
            return True
        if event.event_type == "tool.call.end":
            tool_name = str(event.payload.get("tool_name") or "").lower()
            return any(token in tool_name for token in ("http", "upload", "post", "write", "send"))
        return False

    async def analyze(self, event: EventEnvelope, context: dict[str, Any]) -> DetectorSignal | None:
        run_state = context.setdefault(event.run_id, {})
        text = extract_text(event.payload)
        sensitive_hits = self._scan_sensitive(text)

        if sensitive_hits:
            run_state["last_sensitive"] = {
                "timestamp": event.timestamp,
                "hits": sensitive_hits,
                "event_id": event.event_id,
            }
            outbound = self._is_outbound_event(event)
            return DetectorSignal(
                detector_id=self.detector_id,
                category="data_exfiltration",
                score=0.95 if outbound else clamp(0.35 + 0.08 * len(sensitive_hits)),
                evidence={
                    "reason": "sensitive_content_outbound" if outbound else "sensitive_content_observed",
                    "sensitive_hits": sensitive_hits,
                },
                related_event_ids=[event.event_id],
            )

        if not self._is_outbound_event(event):
            return None

        outbound_bytes = len(text.encode("utf-8")) if text else 0
        destination = (
            str(event.payload.get("url") or event.payload.get("destination") or event.payload.get("path") or "")
        )

        last_sensitive = run_state.get("last_sensitive")
        if not last_sensitive:
            if outbound_bytes >= self.outbound_size_threshold * 2:
                return DetectorSignal(
                    detector_id=self.detector_id,
                    category="data_exfiltration",
                    score=0.55,
                    evidence={
                        "reason": "large_outbound_payload",
                        "outbound_bytes": outbound_bytes,
                        "destination": destination,
                    },
                    related_event_ids=[event.event_id],
                )
            return None

        recent = timedelta(0) <= (event.timestamp - last_sensitive["timestamp"]) <= self.sensitive_window
        if not recent:
            return None

        score = 0.7
        if outbound_bytes >= self.outbound_size_threshold:
            score += 0.15
        score = clamp(score)

        related = [event.event_id]
        if sensitive_event_id := last_sensitive.get("event_id"):
            related.append(sensitive_event_id)

        return DetectorSignal(
            detector_id=self.detector_id,
            category="data_exfiltration",
            score=score,
            evidence={
                "reason": "outbound_after_sensitive_observation",
                "outbound_bytes": outbound_bytes,
                "destination": destination,
                "sensitive_hits": last_sensitive.get("hits", []),
            },
            related_event_ids=related,
        )
