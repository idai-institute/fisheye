from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

from fisheye.detectors.base import Detector, DetectorSignal
from fisheye.detectors.utils import clamp, extract_text
from fisheye.schema.events import EventEnvelope


class PromptInjectionDetector(Detector):
    detector_id = "prompt_injection_rules"
    supported_event_types = (
        "llm.request",
        "llm.message",
        "tool.call.start",
        "tool.call.end",
    )

    def __init__(self, lookback_seconds: int = 120) -> None:
        self.lookback = timedelta(seconds=lookback_seconds)
        self.patterns: tuple[tuple[str, re.Pattern[str]], ...] = (
            ("ignore_previous", re.compile(r"(?i)ignore (all )?(previous|prior) instructions")),
            ("policy_bypass", re.compile(r"(?i)bypass (safety|policy|guardrails?)")),
            ("reveal_secrets", re.compile(r"(?i)reveal (the )?(system prompt|secrets?|keys?)")),
            ("role_override", re.compile(r"(?i)you are now (system|developer|admin)")),
            ("jailbreak", re.compile(r"(?i)jailbreak|dan mode|do anything now")),
        )
        self.privileged_tools = {
            "shell",
            "http",
            "web_request",
            "database_query",
            "file_write",
            "python_exec",
        }

    async def analyze(self, event: EventEnvelope, context: dict[str, Any]) -> DetectorSignal | None:
        run_state = context.setdefault(event.run_id, {})

        if event.event_type in {"llm.request", "llm.message", "tool.call.end"}:
            if event.payload.get("quoted") or event.payload.get("trust") == "trusted":
                return None
            text = extract_text(event.payload)
            matches: list[str] = []
            for rule_id, pattern in self.patterns:
                if pattern.search(text):
                    matches.append(rule_id)

            if not matches:
                return None

            run_state["last_injection_ts"] = event.timestamp
            run_state["injection_event_id"] = event.event_id
            score = clamp(0.25 + 0.2 * len(matches))
            return DetectorSignal(
                detector_id=self.detector_id,
                category="prompt_injection",
                score=score,
                evidence={
                    "matched_rules": matches,
                    "event_type": event.event_type,
                    "sample": text[:240],
                },
                related_event_ids=[event.event_id],
            )

        if event.event_type.startswith("tool.call"):
            last_injection = run_state.get("last_injection_ts")
            if not last_injection:
                return None
            if not timedelta(0) <= (event.timestamp - last_injection) <= self.lookback:
                return None

            tool_name = str(event.payload.get("tool_name") or event.payload.get("name") or "").lower()
            if tool_name in self.privileged_tools:
                return DetectorSignal(
                    detector_id=self.detector_id,
                    category="prompt_injection",
                    score=0.82,
                    evidence={
                        "reason": "privileged_tool_after_injection",
                        "tool_name": tool_name,
                        "lookback_seconds": int(self.lookback.total_seconds()),
                    },
                    related_event_ids=[run_state["injection_event_id"], event.event_id],
                )
        return None
