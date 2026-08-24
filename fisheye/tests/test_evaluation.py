import asyncio
from copy import deepcopy

import pytest

from fisheye.evaluation import compare, evaluate, replay
from fisheye.evaluation.scenarios import corpus


def test_labeled_corpus_and_deterministic_replay():
    async def run():
        cases = corpus()
        report = await evaluate(cases)
        assert report["passed"] == report["scenarios"]
        assert report["false_findings_per_100_benign"] == 0
        one = await replay(cases[0]["events"])
        two = await replay(cases[0]["events"])
        assert one["findings"] == two["findings"]
        assert compare(one, two) == dict(added=[], removed=[], changed=[])

    asyncio.run(run())


def test_conflicting_secrets_are_compared_before_redaction():
    async def run():
        event = dict(
            event_id="same",
            event_type="llm.request",
            agent_id="a",
            run_id="r",
            timestamp="2026-01-01T00:00:00Z",
            payload={"password": "first-secret"},
        )
        changed = dict(event, payload={"password": "second-secret"})
        with pytest.raises(ValueError, match="Conflicting duplicate"):
            await replay([event, changed])

    asyncio.run(run())


def test_evaluation_does_not_pass_benign_case_with_failed_or_missing_analysis():
    from fisheye.analysis import AnalysisProcessor
    from fisheye.detectors.base import Detector, DetectorSignal

    class Broken(Detector):
        detector_id = "broken"

        async def analyze(self, event, context):
            raise ValueError("failed")

    class Incomplete(Detector):
        detector_id = "incomplete"

        async def analyze(self, event, context):
            return DetectorSignal(detector_id=self.detector_id, category="x", score=0, coverage="insufficient_input")

    async def run():
        scenario = dict(
            name="benign",
            events=[dict(event_type="agent.start", agent_id="a", run_id="r")],
            expected_categories=[],
            assessed_categories=["x"],
        )
        for detector in [Broken(), Incomplete()]:
            report = await evaluate([scenario], lambda: AnalysisProcessor(detectors=[detector]))
            assert report["passed"] == 0
            assert report["results"][0]["coverage_issues"]

    asyncio.run(run())


def test_false_finding_rate_counts_findings_and_actual_workflows():
    from fisheye.analysis import AnalysisProcessor
    from fisheye.tests.test_finding_consistency import Always

    async def run():
        events = [
            dict(event_type="agent.start", agent_id=agent, run_id=workflow)
            for agent, workflow in [("a", "one"), ("b", "one"), ("c", "two")]
        ]
        report = await evaluate(
            [dict(name="benign", events=events, expected_categories=[], assessed_categories=["test_incident"])],
            lambda: AnalysisProcessor(detectors=[Always()]),
        )
        assert report["benign_workflows"] == 2
        assert report["false_findings_per_100_benign"] == 150

    asyncio.run(run())


def test_comparison_preserves_environment_and_reports_evidence_changes():
    item = dict(
        finding_id="f",
        application_id="app",
        environment="prod",
        workflow_id="w",
        category="x",
        event_ids=["event"],
        score=0.9,
        evidence={"reason": "original"},
    )
    staging = dict(item, environment="staging")
    before = {"findings": [item, staging]}
    after = deepcopy(before)
    after["findings"][0]["evidence"] = {"reason": "updated"}
    report = compare(before, after)
    assert not report["added"] and not report["removed"]
    assert len(report["changed"]) == 1
    assert report["changed"][0]["after"]["environment"] == "prod"


def test_comparison_tracks_new_evidence_under_stable_finding_identity():
    finding = dict(
        finding_id="f", application_id="app", workflow_id="w", category="x", event_ids=["old"], occurrences=1
    )
    updated = dict(finding, event_ids=["old", "new"], occurrences=2, config_version="changed")
    report = compare({"findings": [finding]}, {"findings": [updated]})
    assert report == {"added": [], "removed": [], "changed": [{"before": finding, "after": updated}]}
    with pytest.raises(ValueError, match="Duplicate finding identity"):
        compare({"findings": [finding, updated]}, {"findings": []})
    legacy = dict(finding)
    legacy.pop("finding_id")
    assert compare({"findings": [legacy]}, {"findings": [legacy]}) == {"added": [], "removed": [], "changed": []}
