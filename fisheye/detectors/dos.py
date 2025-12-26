from __future__ import annotations

from collections import Counter, deque
from datetime import timedelta
from typing import Any

from fisheye.detectors.base import Detector, DetectorSignal
from fisheye.detectors.utils import clamp
from fisheye.schema.events import EventEnvelope


class DoSDetector(Detector):
    detector_id = "dos_rules"
    supported_event_types = (
        "tool.call.start",
        "tool.call.error",
        "agent.error",
        "llm.request",
        "llm.response",
    )

    def __init__(
        self,
        burst_window_seconds: int = 10,
        burst_threshold: int = 15,
        repeat_window_size: int = 20,
        repeat_threshold: int = 12,
        token_threshold: int = 3000,
    ) -> None:
        self.burst_window = timedelta(seconds=burst_window_seconds)
        self.burst_threshold = burst_threshold
        self.repeat_window_size = repeat_window_size
        self.repeat_threshold = repeat_threshold
        self.token_threshold = token_threshold

    async def analyze(self, event: EventEnvelope, context: dict[str, Any]) -> DetectorSignal | None:
        run_state = context.setdefault(
            event.run_id,
            {
                "tool_call_times": deque(),
                "tool_call_signatures": deque(maxlen=self.repeat_window_size),
                "error_times": deque(),
            },
        )

        if event.event_type == "tool.call.start":
            return self._analyze_tool_burst(event, run_state)

        if event.event_type in {"tool.call.error", "agent.error"}:
            return self._analyze_errors(event, run_state)

        if event.event_type in {"llm.request", "llm.response"}:
            return self._analyze_tokens(event)

        return None

    def _analyze_tool_burst(self, event: EventEnvelope, run_state: dict[str, Any]) -> DetectorSignal | None:
        now = event.timestamp
        times: deque = run_state["tool_call_times"]
        times.append(now)

        while times and (now - times[0]) > self.burst_window:
            times.popleft()

        signature = self._signature(event)
        signatures: deque = run_state["tool_call_signatures"]
        signatures.append(signature)
        repetitive_count = Counter(signatures).most_common(1)[0][1] if signatures else 0

        burst_score = clamp((len(times) - self.burst_threshold) / max(self.burst_threshold, 1))
        repeat_score = clamp((repetitive_count - self.repeat_threshold) / max(self.repeat_threshold, 1))
        score = clamp(max(burst_score, repeat_score))

        if score <= 0.0:
            return None

        return DetectorSignal(
            detector_id=self.detector_id,
            category="dos",
            score=score,
            evidence={
                "reason": "tool_call_burst_or_loop",
                "calls_in_window": len(times),
                "burst_window_seconds": int(self.burst_window.total_seconds()),
                "repetitive_signature_count": repetitive_count,
                "signature": signature,
            },
            related_event_ids=[event.event_id],
        )

    def _analyze_errors(self, event: EventEnvelope, run_state: dict[str, Any]) -> DetectorSignal | None:
        now = event.timestamp
        errors: deque = run_state["error_times"]
        errors.append(now)

        while errors and (now - errors[0]) > self.burst_window:
            errors.popleft()

        score = clamp((len(errors) - 5) / 5)
        if score <= 0.0:
            return None

        return DetectorSignal(
            detector_id=self.detector_id,
            category="dos",
            score=score,
            evidence={
                "reason": "error_storm",
                "errors_in_window": len(errors),
                "window_seconds": int(self.burst_window.total_seconds()),
            },
            related_event_ids=[event.event_id],
        )

    def _analyze_tokens(self, event: EventEnvelope) -> DetectorSignal | None:
        value = event.payload.get("token_count")
        if value is None:
            value = event.meta.get("features", {}).get("approx_tokens")
        if value is None:
            return None

        try:
            tokens = int(value)
        except (TypeError, ValueError):
            return None

        score = clamp((tokens - self.token_threshold) / max(self.token_threshold, 1))
        if score <= 0.0:
            return None

        return DetectorSignal(
            detector_id=self.detector_id,
            category="dos",
            score=score,
            evidence={
                "reason": "token_explosion",
                "token_count": tokens,
                "token_threshold": self.token_threshold,
            },
            related_event_ids=[event.event_id],
        )

    @staticmethod
    def _signature(event: EventEnvelope) -> str:
        tool_name = str(event.payload.get("tool_name") or event.payload.get("name") or "")
        args = str(event.payload.get("arguments") or event.payload.get("input") or "")
        return f"{tool_name}:{args[:80]}"
