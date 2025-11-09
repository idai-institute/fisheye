from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fisheye.collectors.base import AlertSink, Collector
from fisheye.collectors.sqlite_store import SQLiteStore
from fisheye.detectors.base import Detector, DetectorSignal
from fisheye.detectors.score_aggregation import combine_signals
from fisheye.schema.alerts import Alert
from fisheye.schema.events import EventEnvelope


class DetectorEngine(Collector):
    name = "detector_engine"

    def __init__(
        self,
        detectors: list[Detector],
        store: SQLiteStore | None = None,
        alert_sinks: list[AlertSink] | None = None,
        thresholds: dict[str, float] | None = None,
        detector_weights: dict[str, float] | None = None,
    ) -> None:
        self.detectors = detectors
        self.store = store
        self.alert_sinks = alert_sinks or []
        self.thresholds = thresholds or {
            "prompt_injection": 0.7,
            "data_exfiltration": 0.7,
            "dos": 0.7,
        }
        self.detector_weights = detector_weights or {}
        self._contexts: dict[str, dict[str, Any]] = {detector.detector_id: {} for detector in detectors}

    async def handle_event(self, event: EventEnvelope) -> None:
        signals: list[DetectorSignal] = []

        for detector in self.detectors:
            if detector.supported_event_types and event.event_type not in detector.supported_event_types:
                continue

            signal = await detector.analyze(event, self._contexts[detector.detector_id])
            if signal is None:
                continue
            signals.append(signal)
            if self.store:
                await self.store.record_detector_signal(signal, event)

        if not signals:
            return

        grouped: dict[str, list[DetectorSignal]] = defaultdict(list)
        for signal in signals:
            grouped[signal.category].append(signal)

        for category, category_signals in grouped.items():
            score = combine_signals(category_signals, self.detector_weights)
            threshold = self.thresholds.get(category, 0.7)
            alert = Alert(
                alert_id=uuid4().hex,
                timestamp=datetime.now(timezone.utc),
                agent_id=event.agent_id,
                run_id=event.run_id,
                category=category,  # type: ignore[arg-type]
                score=score,
                threshold=threshold,
                triggered=score >= threshold,
                sources=[signal.detector_id for signal in category_signals],
                evidence={
                    "signals": [signal.model_dump(mode="json") for signal in category_signals],
                },
                related_event_ids=sorted(
                    {
                        event.event_id,
                        *[item for signal in category_signals for item in signal.related_event_ids],
                    }
                ),
            )
            await self._emit_alert(alert)

    async def _emit_alert(self, alert: Alert) -> None:
        if self.store:
            await self.store.handle_alert(alert)
        for sink in self.alert_sinks:
            await sink.handle_alert(alert)

    def list_detector_ids(self) -> list[str]:
        return [detector.detector_id for detector in self.detectors]
