"""Offline replay never invokes agent tools or model providers."""

from __future__ import annotations

import hashlib
import json
import time
from collections import defaultdict
from pathlib import Path

from fisheye.analysis import AnalysisProcessor
from fisheye.findings import merge_finding
from fisheye.privacy import capture_event
from fisheye.schema.events import EventEnvelope


def load_events(path):
    with Path(path).open() as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                yield EventEnvelope.model_validate(data.get("event", data))
            except Exception as exc:
                raise ValueError(f"Invalid event on line {line_number}: {type(exc).__name__}") from exc


async def replay(events, processor=None):
    processor = processor or AnalysisProcessor()
    states, findings, seen = {}, {}, {}
    count = 0
    latencies = []
    errors = []
    coverage_issues = defaultdict(set)
    for raw in events:
        event = raw if isinstance(raw, EventEnvelope) else EventEnvelope.model_validate(raw)
        if "observed_at" not in event.model_fields_set:
            event = event.model_copy(update={"observed_at": event.timestamp})
        # Compare original input before redaction can conceal a conflicting retry.
        fingerprint = hashlib.sha256(
            json.dumps(event.model_dump(mode="json", exclude={"observed_at"}), sort_keys=True).encode()
        ).hexdigest()
        if event.event_id in seen:
            if seen[event.event_id] != fingerprint:
                raise ValueError("Conflicting duplicate event ID during replay")
            continue
        seen[event.event_id] = fingerprint
        # Recorded annotations are trusted only by the offline evaluator.
        if "_analysis" not in event.meta:
            event = capture_event(event)
        started = time.perf_counter()
        result = await processor.analyze(event, states.get(event.scope))
        latencies.append((time.perf_counter() - started) * 1000)
        states[event.scope] = result.state
        for finding in result.findings:
            findings[finding.finding_id] = merge_finding(findings.get(finding.finding_id), finding)
        errors.extend(result.errors)
        for plugin, status in processor.coverage.items():
            if status not in {"evaluated", "not_applicable"}:
                coverage_issues[plugin].add(status)
        count += 1
    latencies.sort()

    def quantile(q):
        return latencies[min(int((len(latencies) - 1) * q), len(latencies) - 1)] if latencies else 0

    return dict(
        events=count,
        workflows=len(states),
        findings=[f.model_dump(mode="json") for f in findings.values()],
        errors=errors,
        coverage=processor.coverage,
        coverage_issues={plugin: sorted(statuses) for plugin, statuses in coverage_issues.items()},
        config_version=processor.config_version,
        detector_versions={
            d.detector_id: getattr(getattr(d, "spec", None), "version", "1") for d in processor.detectors
        },
        analysis_latency_ms=dict(p50=quantile(0.5), p95=quantile(0.95), p99=quantile(0.99)),
    )


async def evaluate(scenarios, processor_factory=AnalysisProcessor):
    rows = []
    totals = defaultdict(lambda: dict(tp=0, fp=0, fn=0))
    benign = benign_false = 0
    for scenario in scenarios:
        report = await replay(scenario["events"], processor_factory())
        expected = set(scenario.get("expected_categories", []))
        # A scenario may label selected categories; others are reported but unscored.
        assessed = set(scenario.get("assessed_categories", expected))
        if not expected <= assessed:
            raise ValueError("Expected categories must be included in assessed_categories")
        assessed_findings = [f for f in report["findings"] if f["category"] in assessed]
        actual = {f["category"] for f in assessed_findings}
        for category in assessed:
            totals[category]["tp"] += int(category in actual and category in expected)
            totals[category]["fp"] += int(category in actual and category not in expected)
            totals[category]["fn"] += int(category not in actual and category in expected)
        if not expected:
            benign += report["workflows"]
            benign_false += len(assessed_findings)
        rows.append(
            dict(
                name=scenario["name"],
                split=scenario.get("split", "test"),
                expected=sorted(expected),
                actual=sorted(actual),
                passed=actual == expected
                and not report["errors"]
                and not report["coverage_issues"]
                and report["events"] > 0,
                findings=report["findings"],
                errors=report["errors"],
                coverage_issues=report["coverage_issues"],
            )
        )
    metrics = {
        key: dict(
            value,
            precision=value["tp"] / (value["tp"] + value["fp"]) if value["tp"] + value["fp"] else None,
            recall=value["tp"] / (value["tp"] + value["fn"]) if value["tp"] + value["fn"] else None,
        )
        for key, value in totals.items()
    }
    return dict(
        scenarios=len(rows),
        passed=sum(r["passed"] for r in rows),
        metrics=metrics,
        benign_workflows=benign,
        false_findings_per_100_benign=100 * benign_false / benign if benign else None,
        results=rows,
    )


def compare(before, after):
    def key(f):
        return (
            f["application_id"],
            f.get("environment", "local"),
            f["workflow_id"],
            f["category"],
            f.get("finding_id", ""),
            tuple(sorted(f["event_ids"])),
        )

    old = {key(f): f for f in before["findings"]}
    new = {key(f): f for f in after["findings"]}
    return dict(
        added=[new[k] for k in sorted(new.keys() - old.keys())],
        removed=[old[k] for k in sorted(old.keys() - new.keys())],
        changed=[
            dict(before=old[k], after=new[k])
            for k in sorted(new.keys() & old.keys())
            if any(
                old[k].get(field) != new[k].get(field)
                for field in ("score", "severity", "title", "evidence", "status", "agent_ids", "evidence_truncated")
            )
        ],
    )
