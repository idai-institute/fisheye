"""Deterministic per-workflow analysis with isolated plugin failures."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass

from fisheye.behavior.monitor import StatisticalBehaviorMonitor
from fisheye.detectors.dos import DoSDetector
from fisheye.detectors.exfiltration import DataExfiltrationDetector
from fisheye.detectors.prompt_injection import PromptInjectionDetector
from fisheye.detectors.score_aggregation import combine_signals
from fisheye.graph import WorkflowGraph
from fisheye.schema.alerts import Alert
from fisheye.schema.domain import Finding, PluginSpec
from fisheye.state.codec import decode, encode


@dataclass
class AnalysisResult:
    state: dict
    alerts: list
    findings: list
    signals: list
    errors: list


class _AlertBuffer:
    def __init__(self):
        self.alerts = []

    async def handle_alert(self, alert):
        self.alerts.append(alert)


class AnalysisProcessor:
    def __init__(
        self,
        thresholds=None,
        detector_weights=None,
        detectors=None,
        graph=None,
        config_version="",
        min_samples=10,
        frozen=False,
    ):
        self.detectors = (
            detectors
            if detectors is not None
            else [PromptInjectionDetector(), DataExfiltrationDetector(), DoSDetector()]
        )
        self.thresholds = thresholds or dict(prompt_injection=0.7, data_exfiltration=0.7, dos=0.7, behavioral=0.75)
        self.weights = detector_weights or {}
        self.graph = graph or WorkflowGraph()
        self.config_version = config_version
        self.coverage = {}
        self.min_samples, self.frozen = min_samples, frozen

    def list_detector_ids(self):
        return [d.detector_id for d in self.detectors]

    async def analyze(self, event, checkpoint=None):
        if checkpoint and checkpoint.get("version") != 2:
            raise ValueError("Unsupported analysis checkpoint version")
        state = decode(checkpoint["data"]) if checkpoint else {}
        legacy_contexts = state.pop("detectors", {})
        contexts = state.setdefault("plugin_contexts", {})
        versions = state.setdefault("plugin_versions", {})
        signals, errors = [], []
        scoped = event.model_copy(update={"run_id": event.scope})
        for detector in self.detectors:
            if detector.supported_event_types and event.event_type not in detector.supported_event_types:
                continue
            spec = getattr(detector, "spec", PluginSpec(plugin_id=detector.detector_id))
            if spec.event_types and event.event_type not in spec.event_types:
                continue
            if versions.get(detector.detector_id) != spec.version:
                contexts.pop(detector.detector_id, None)
                versions[detector.detector_id] = spec.version
            if event.schema_version not in spec.schema_versions or (
                spec.requires_content and event.meta.get("feature_only")
            ):
                self.coverage[detector.detector_id] = "insufficient_input"
                continue
            bucket = contexts.setdefault(detector.detector_id, {})
            now = event.observed_at.timestamp()
            for key in list(bucket):
                if now - bucket[key]["updated"] > spec.state_ttl_seconds:
                    del bucket[key]
            scope_key = (
                json.dumps([event.agent_id, event.agent_version, event.agent_instance_id])
                if spec.scope == "agent"
                else "workflow"
            )
            previous = bucket.get(scope_key, {}).get("state", legacy_contexts.get(detector.detector_id, {}))
            context = copy.deepcopy(previous)
            try:
                signal = await asyncio.wait_for(detector.analyze(scoped, context), spec.timeout_seconds)
                updated = dict(bucket, **{scope_key: {"state": context, "updated": now}})
                if len(json.dumps(encode(updated), allow_nan=False).encode()) > spec.max_state_bytes:
                    raise ValueError("Plugin checkpoint exceeds configured size")
                contexts[detector.detector_id] = updated
                self.coverage[detector.detector_id] = signal.coverage if signal else "evaluated"
                if signal:
                    signals.append(signal)
            except Exception as exc:
                if budget_key := getattr(detector, "budget_context_key", None):
                    fallback = copy.deepcopy(previous)
                    fallback[budget_key] = context.get(budget_key, 0)
                    bucket[scope_key] = {"state": fallback, "updated": now}
                errors.append((detector.detector_id, type(exc).__name__))
                self.coverage[detector.detector_id] = "error"
        grouped = defaultdict(list)
        for signal in signals:
            grouped[signal.category].append(signal)
        alerts = []
        for category, items in grouped.items():
            score = combine_signals(items, self.weights)
            threshold = self.thresholds.get(category, 0.7)
            alerts.append(
                Alert(
                    alert_id=hashlib.sha256(f"{event.event_id}:{category}".encode()).hexdigest()[:32],
                    timestamp=event.timestamp,
                    agent_id=event.agent_id,
                    run_id=event.run_id,
                    category=category,
                    score=score,
                    threshold=threshold,
                    triggered=score >= threshold,
                    sources=[s.detector_id for s in items],
                    evidence={"signals": [s.model_dump(mode="json") for s in items]},
                    related_event_ids=sorted({event.event_id, *[i for s in items for i in s.related_event_ids]}),
                )
            )
        sink = _AlertBuffer()
        monitor = StatisticalBehaviorMonitor(
            alert_sinks=[sink],
            threshold=self.thresholds.get("behavioral", 0.75),
            min_samples=self.min_samples,
            frozen=self.frozen,
        )
        for key, value in state.get("behavior", {}).items():
            getattr(monitor, key).update(value)
        await monitor.handle_event(event)
        state["behavior"] = {k: dict(v) for k, v in vars(monitor).items() if k.startswith("_") and isinstance(v, dict)}
        for i, alert in enumerate(sink.alerts):
            alert.alert_id = hashlib.sha256(f"{event.event_id}:behavior:{i}".encode()).hexdigest()[:32]
            alert.timestamp = event.timestamp
            alerts.append(alert)
        findings = self.graph.process(event, state.setdefault("graph", {}))
        for alert in alerts:
            if alert.triggered:
                key = f"{event.scope}:{alert.category}:{event.agent_id}:{alert.sources}"
                findings.append(
                    Finding(
                        finding_id=hashlib.sha256(key.encode()).hexdigest()[:32],
                        application_id=event.application_id,
                        workflow_id=event.workflow,
                        category=alert.category,
                        severity="high",
                        score=alert.score,
                        title=f"{alert.category.replace('_', ' ')} detected",
                        agent_ids=[event.agent_id],
                        event_ids=alert.related_event_ids,
                        evidence=alert.evidence,
                        first_seen=event.timestamp,
                        last_seen=event.timestamp,
                    )
                )
        for finding in findings:
            finding.config_version = self.config_version
        return AnalysisResult(dict(version=2, data=encode(state)), alerts, findings, signals, errors)
