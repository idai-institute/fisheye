import asyncio
from datetime import datetime, timezone

from fisheye.detectors.base import Detector, DetectorSignal
from fisheye.evaluation import replay
from fisheye.findings import merge_finding
from fisheye.runtime import build_analysis, build_default_runtime
from fisheye.schema.domain import Finding
from fisheye.schema.events import EventEnvelope
from fisheye.tests.test_privacy_and_auth import config


class Always(Detector):
    detector_id = "test_incident"

    async def analyze(self, event, context):
        return DetectorSignal(
            detector_id=self.detector_id, category="test_incident", score=0.9, related_event_ids=[event.event_id]
        )


def event(day, event_id):
    return EventEnvelope(
        event_id=event_id,
        event_type="agent.start",
        agent_id="a",
        run_id="r",
        environment="production",
        timestamp=datetime(2026, 1, day, tzinfo=timezone.utc),
    )


def test_live_and_replay_merge_late_observations_identically(tmp_path):
    async def run():
        cfg = config(tmp_path)
        events = [event(2, "later"), event(1, "earlier")]
        async with build_default_runtime(cfg) as runtime:
            runtime.analysis.detectors = [Always()]
            await runtime.ingest(events)
            await runtime.drain(3)
            live = await runtime.store.list_findings()
        processor = build_analysis(cfg)
        processor.detectors = [Always()]
        replayed = await replay(events, processor)
        assert replayed["findings"] == live
        assert live[0]["environment"] == "production"
        assert live[0]["first_seen"].startswith("2026-01-01")
        assert live[0]["last_seen"].startswith("2026-01-02")

    asyncio.run(run())


def test_new_occurrence_reopens_resolved_but_preserves_false_positive(tmp_path):
    async def run():
        async with build_default_runtime(config(tmp_path)) as runtime:
            runtime.analysis.detectors = [Always()]
            await runtime.publish(event(1, "first"))
            await runtime.drain(3)
            finding = (await runtime.store.list_findings())[0]
            await runtime.store.update_finding(finding["finding_id"], "resolved", "operator")
            await runtime.publish(event(2, "second"))
            await runtime.drain(3)
            current = (await runtime.store.list_findings())[0]
            assert current["status"] == "open" and current["occurrences"] == 2
            assert runtime.store._query("SELECT * FROM audit WHERE operation='finding.reopened'")
            await runtime.store.update_finding(finding["finding_id"], "false_positive", "operator")
            await runtime.publish(event(3, "third"))
            await runtime.drain(3)
            assert (await runtime.store.list_findings())[0]["status"] == "false_positive"

    asyncio.run(run())


def test_bounded_evidence_preserves_current_causal_path_and_agents():
    old = Finding(
        finding_id="f",
        workflow_id="w",
        category="x",
        score=0.8,
        title="Evidence",
        event_ids=["source"] + [f"old-{i}" for i in range(199)],
        agent_ids=["researcher"],
    )
    new = old.model_copy(update={"event_ids": ["source", "transfer", "action"], "agent_ids": ["executor"]})
    merged = merge_finding(old, new)
    assert len(merged.event_ids) == 200 and merged.evidence_truncated
    assert merged.event_ids[-3:] == ["source", "transfer", "action"]
    assert merged.agent_ids == ["executor", "researcher"]
    assert len(old.event_ids) == 200 and old.occurrences == 1
