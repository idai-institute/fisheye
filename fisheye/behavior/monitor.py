from __future__ import annotations

from collections import Counter, defaultdict, deque
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fisheye.behavior.online_stats import EwmaTracker, ToolDistributionTracker
from fisheye.collectors.base import AlertSink, Collector
from fisheye.collectors.sqlite_store import BehaviorStat, SQLiteStore
from fisheye.detectors.utils import clamp
from fisheye.schema.alerts import Alert
from fisheye.schema.events import EventEnvelope


class StatisticalBehaviorMonitor(Collector):
    name = "behavior_monitor"

    def __init__(
        self,
        store: SQLiteStore | None = None,
        alert_sinks: list[AlertSink] | None = None,
        threshold: float = 0.75,
        z_threshold: float = 3.0,
        rate_window_seconds: int = 30,
        min_samples: int = 1,
        frozen: bool = False,
    ) -> None:
        self.store = store
        self.alert_sinks = alert_sinks or []
        self.threshold = threshold
        self.z_threshold = z_threshold
        self.rate_window = timedelta(seconds=rate_window_seconds)
        self.min_samples = min_samples
        self.frozen = frozen

        self._tool_call_times: dict[tuple[str, str], deque[datetime]] = defaultdict(lambda: deque(maxlen=10000))
        self._event_outcomes: dict[str, deque[tuple[datetime, int]]] = defaultdict(lambda: deque(maxlen=10000))
        self._tool_recent: dict[str, deque[str]] = defaultdict(lambda: deque(maxlen=100))

        def factory():
            return EwmaTracker(min_samples=min_samples, frozen=frozen)

        self._tool_rate_stats: dict[tuple[str, str], EwmaTracker] = defaultdict(factory)
        self._latency_stats: dict[tuple[str, str], EwmaTracker] = defaultdict(factory)
        self._token_stats: dict[str, EwmaTracker] = defaultdict(factory)
        self._error_rate_stats: dict[str, EwmaTracker] = defaultdict(factory)
        self._tool_dist_stats: dict[str, ToolDistributionTracker] = defaultdict(ToolDistributionTracker)

    async def handle_event(self, event: EventEnvelope) -> None:
        # Keep high-cardinality tool/agent dimensions bounded inside a workflow.
        for value in vars(self).values():
            if isinstance(value, defaultdict) and len(value) > 1000:
                for key in list(value)[: len(value) - 1000]:
                    del value[key]
        alerts: list[Alert] = []

        rate_alert = await self._observe_tool_rate(event)
        if rate_alert:
            alerts.append(rate_alert)

        latency_alert = await self._observe_latency(event)
        if latency_alert:
            alerts.append(latency_alert)

        token_alert = await self._observe_tokens(event)
        if token_alert:
            alerts.append(token_alert)

        error_alert = await self._observe_error_rate(event)
        if error_alert:
            alerts.append(error_alert)

        dist_alert = await self._observe_tool_distribution(event)
        if dist_alert:
            alerts.append(dist_alert)

        for alert in alerts:
            if self.store:
                await self.store.handle_alert(alert)
            for sink in self.alert_sinks:
                await sink.handle_alert(alert)

    async def _observe_tool_rate(self, event: EventEnvelope) -> Alert | None:
        if event.event_type != "tool.call.start":
            return None

        tool_name = str(event.payload.get("tool_name") or event.payload.get("name") or "unknown")
        key = (event.agent_id, tool_name)
        points = self._tool_call_times[key]
        points.append(event.timestamp)
        while points and (event.timestamp - points[0]) > self.rate_window:
            points.popleft()

        rate = len(points) / max(self.rate_window.total_seconds(), 1)
        tracker = self._tool_rate_stats[key]
        z = tracker.update(rate)
        await self._store_behavior_stat(event, "tool_rate", rate, z, tool_name)

        return self._maybe_alert(
            event,
            metric="tool_rate",
            value=rate,
            zscore=z,
            tool_name=tool_name,
            related_event_ids=[event.event_id],
        )

    async def _observe_latency(self, event: EventEnvelope) -> Alert | None:
        if event.event_type not in {"tool.call.end", "llm.response"}:
            return None

        raw = event.payload.get("latency_ms")
        if raw is None:
            return None
        try:
            latency = float(raw)
        except (TypeError, ValueError):
            return None

        tool_name = str(event.payload.get("tool_name") or "llm")
        key = (event.agent_id, tool_name)
        tracker = self._latency_stats[key]
        z = tracker.update(latency)
        await self._store_behavior_stat(event, "latency_ms", latency, z, tool_name)

        return self._maybe_alert(
            event,
            metric="latency_ms",
            value=latency,
            zscore=z,
            tool_name=tool_name,
            related_event_ids=[event.event_id],
        )

    async def _observe_tokens(self, event: EventEnvelope) -> Alert | None:
        if event.event_type not in {"llm.request", "llm.response"}:
            return None

        raw = event.payload.get("token_count")
        if raw is None:
            raw = event.meta.get("features", {}).get("approx_tokens")
        if raw is None:
            return None

        try:
            tokens = float(raw)
        except (TypeError, ValueError):
            return None

        tracker = self._token_stats[event.agent_id]
        z = tracker.update(tokens)
        await self._store_behavior_stat(event, "token_count", tokens, z)

        return self._maybe_alert(
            event,
            metric="token_count",
            value=tokens,
            zscore=z,
            related_event_ids=[event.event_id],
        )

    async def _observe_error_rate(self, event: EventEnvelope) -> Alert | None:
        if event.event_type not in {"tool.call.end", "tool.call.error"}:
            return None
        history = self._event_outcomes[event.agent_id]
        is_error = 1 if event.event_type in {"agent.error", "tool.call.error"} else 0
        history.append((event.timestamp, is_error))

        while history and (event.timestamp - history[0][0]) > self.rate_window:
            history.popleft()

        if not history:
            return None

        errors = sum(item[1] for item in history)
        rate = errors / len(history)
        tracker = self._error_rate_stats[event.agent_id]
        z = tracker.update(rate)
        await self._store_behavior_stat(event, "error_rate", rate, z)

        return self._maybe_alert(
            event,
            metric="error_rate",
            value=rate,
            zscore=z,
            related_event_ids=[event.event_id],
        )

    async def _observe_tool_distribution(self, event: EventEnvelope) -> Alert | None:
        if event.event_type != "tool.call.start":
            return None

        tool_name = str(event.payload.get("tool_name") or event.payload.get("name") or "unknown")
        recent = self._tool_recent[event.agent_id]
        recent.append(tool_name)
        counts = Counter(recent)
        total = max(sum(counts.values()), 1)
        distribution = {name: count / total for name, count in counts.items()}

        tracker = self._tool_dist_stats[event.agent_id]
        if self.frozen and tracker.baseline:
            keys = set(distribution) | set(tracker.baseline)
            drift = min(1.0, sum(abs(distribution.get(k, 0) - tracker.baseline.get(k, 0)) for k in keys) / 2)
        else:
            drift = tracker.update(distribution)
        await self._store_behavior_stat(event, "tool_distribution_drift", drift, drift, tool_name)

        if drift < self.threshold:
            return None

        return Alert(
            alert_id=uuid4().hex,
            timestamp=datetime.now(timezone.utc),
            agent_id=event.agent_id,
            run_id=event.run_id,
            category="behavioral",
            score=drift,
            threshold=self.threshold,
            triggered=True,
            sources=["behavior.tool_distribution"],
            evidence={
                "metric": "tool_distribution_drift",
                "drift": drift,
                "distribution": distribution,
            },
            related_event_ids=[event.event_id],
        )

    async def _store_behavior_stat(
        self,
        event: EventEnvelope,
        metric_name: str,
        value: float,
        zscore: float,
        tool_name: str | None = None,
    ) -> None:
        if not self.store:
            return
        await self.store.record_behavior_stat(
            BehaviorStat(
                timestamp=event.timestamp.isoformat(),
                agent_id=event.agent_id,
                run_id=event.run_id,
                metric_name=metric_name,
                value=float(value),
                zscore=float(zscore),
                tool_name=tool_name,
            )
        )

    def _maybe_alert(
        self,
        event: EventEnvelope,
        metric: str,
        value: float,
        zscore: float,
        related_event_ids: list[str],
        tool_name: str | None = None,
    ) -> Alert | None:
        if abs(zscore) < self.z_threshold:
            return None

        score = clamp(abs(zscore) / (self.z_threshold * 2.0))
        return Alert(
            alert_id=uuid4().hex,
            timestamp=datetime.now(timezone.utc),
            agent_id=event.agent_id,
            run_id=event.run_id,
            category="behavioral",
            score=score,
            threshold=self.threshold,
            triggered=score >= self.threshold,
            sources=["behavior.monitor"],
            evidence={
                "metric": metric,
                "value": value,
                "zscore": zscore,
                "tool_name": tool_name,
            },
            related_event_ids=related_event_ids,
        )
