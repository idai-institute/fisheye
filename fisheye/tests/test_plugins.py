import asyncio

from fisheye.analysis import AnalysisProcessor
from fisheye.detectors.semantic import Judgment, SemanticDetector
from fisheye.schema.events import EventEnvelope


def test_semantic_judge_budget_and_feature_coverage():
    class Fake:
        async def evaluate(self, content):
            return Judgment(category="semantic_test", score=0.8, explanation="test evidence")

    async def run():
        detector = SemanticDetector(Fake(), "fake-v1", "p1", max_cost=0.01, cost_per_call=0.01)
        event = EventEnvelope(event_type="llm.request", agent_id="a", run_id="r", payload={"message": "hello"})
        state = {}
        assert (await detector.analyze(event, state)).score == 0.8
        assert (await detector.analyze(event, state)).coverage == "budget_exhausted"
        processor = AnalysisProcessor(detectors=[detector])
        event.meta["feature_only"] = True
        result = await processor.analyze(event)
        assert processor.coverage[detector.detector_id] == "insufficient_input"
        assert result.signals == []

    asyncio.run(run())


def test_agent_scoped_plugin_expiry_and_live_replay_configuration():
    from datetime import timedelta

    from fisheye.config import FisheyeConfig
    from fisheye.detectors.base import Detector, DetectorSignal
    from fisheye.runtime import build_analysis
    from fisheye.schema.domain import PluginSpec

    class Counter(Detector):
        detector_id = "scoped_counter"
        spec = PluginSpec(plugin_id=detector_id, scope="agent", state_ttl_seconds=10)

        async def analyze(self, event, context):
            context["n"] = context.get("n", 0) + 1
            return DetectorSignal(detector_id=self.detector_id, category="count", score=0, evidence={"n": context["n"]})

    async def run():
        processor = AnalysisProcessor(detectors=[Counter()])
        first = EventEnvelope(event_type="agent.start", agent_id="a", run_id="r")
        a = await processor.analyze(first)
        b = await processor.analyze(first.model_copy(update={"agent_id": "b"}), a.state)
        assert b.signals[0].evidence["n"] == 1
        a = await processor.analyze(first, b.state)
        assert a.signals[0].evidence["n"] == 2
        expired = await processor.analyze(
            first.model_copy(update={"observed_at": first.observed_at + timedelta(seconds=11)}), a.state
        )
        assert expired.signals[0].evidence["n"] == 1

    asyncio.run(run())
    cfg = FisheyeConfig.from_dict({"oversight": {"call_budget": 7, "baseline_frozen": True}})
    processor = build_analysis(cfg)
    assert processor.graph.budgets["calls"] == 7 and processor.frozen
