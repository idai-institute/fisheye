"""Opt-in semantic evaluator contract. No provider or network client is bundled."""

from __future__ import annotations

import asyncio
from typing import Protocol

from pydantic import BaseModel, Field

from fisheye.detectors.base import Detector, DetectorSignal
from fisheye.schema.domain import PluginSpec


class Judgment(BaseModel):
    category: str
    score: float = Field(ge=0, le=1, allow_inf_nan=False)
    explanation: str = Field(max_length=2000)
    actual_cost: float = Field(default=0, ge=0, allow_inf_nan=False)


class JudgeProvider(Protocol):
    async def evaluate(self, content: dict) -> Judgment: ...


class SemanticDetector(Detector):
    budget_context_key = "spent"
    supported_event_types = ("llm.request", "llm.message", "tool.call.end")

    def __init__(
        self,
        provider: JudgeProvider,
        model_version: str,
        prompt_version: str,
        max_cost: float = 1.0,
        cost_per_call: float = 0.01,
        timeout_seconds: float = 2,
    ):
        if max_cost <= 0 or cost_per_call <= 0:
            raise ValueError("Semantic evaluation budgets must be positive")
        self.provider = provider
        self.detector_id = "semantic." + model_version
        self.model_version, self.prompt_version = model_version, prompt_version
        self.max_cost, self.cost_per_call = max_cost, cost_per_call
        self.spec = PluginSpec(
            plugin_id=self.detector_id,
            version=model_version + ":" + prompt_version,
            requires_content=True,
            timeout_seconds=timeout_seconds,
        )

    async def analyze(self, event, context):
        spent = context.get("spent", 0)
        if spent + self.cost_per_call > self.max_cost:
            return DetectorSignal(
                detector_id=self.detector_id,
                category="coverage",
                score=0,
                coverage="budget_exhausted",
                evidence={"reason": "semantic_budget_exhausted"},
            )
        # Treat the input as data; the host provider owns its prompting and policy.
        context["spent"] = spent + self.cost_per_call
        judgment = Judgment.model_validate(
            await asyncio.wait_for(
                self.provider.evaluate(
                    {
                        "event_type": event.event_type,
                        "untrusted_payload": event.payload,
                        "model_version": self.model_version,
                        "prompt_version": self.prompt_version,
                    }
                ),
                self.spec.timeout_seconds,
            )
        )
        context["spent"] = spent + max(self.cost_per_call, judgment.actual_cost)
        return DetectorSignal(
            detector_id=self.detector_id,
            detector_version=self.spec.version,
            category=judgment.category,
            score=judgment.score,
            evidence={
                "explanation": judgment.explanation,
                "model_version": self.model_version,
                "prompt_version": self.prompt_version,
                "advisory": True,
            },
            related_event_ids=[event.event_id],
        )
