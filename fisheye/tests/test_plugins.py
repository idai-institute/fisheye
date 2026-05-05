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
