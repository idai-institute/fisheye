from __future__ import annotations

from pathlib import Path
from typing import Any

from fisheye.behavior.monitor import StatisticalBehaviorMonitor
from fisheye.bus.async_bus import AsyncEventBus
from fisheye.bus.routing import Route, RouteMode, Router
from fisheye.collectors.base import Collector
from fisheye.collectors.jsonl_logger import JsonlLoggerCollector
from fisheye.collectors.sqlite_store import SQLiteStore
from fisheye.config import FisheyeConfig
from fisheye.detectors.dos import DoSDetector
from fisheye.detectors.engine import DetectorEngine
from fisheye.detectors.exfiltration import DataExfiltrationDetector
from fisheye.detectors.prompt_injection import PromptInjectionDetector
from fisheye.preprocessors.base import Preprocessor
from fisheye.preprocessors.buffering import BufferingPreprocessor
from fisheye.preprocessors.embeddings import EmbeddingPreprocessor, LocalHashEmbeddingProvider, NoopEmbeddingProvider
from fisheye.preprocessors.hashing import HashFingerprintPreprocessor
from fisheye.preprocessors.pipeline import PreprocessorPipeline
from fisheye.preprocessors.redaction import PIIRedactionPreprocessor, SecretRedactionPreprocessor
from fisheye.preprocessors.urls import URLDomainExtractionPreprocessor
from fisheye.schema.events import EventEnvelope

_ROUTE_MODE_META_KEY = "_route_mode"


class _RoutedCollector(Collector):
    def __init__(self, collector: Collector, mode: RouteMode) -> None:
        self.collector = collector
        self.mode = mode
        self.name = getattr(collector, "name", collector.__class__.__name__)

    async def handle_event(self, event: EventEnvelope) -> None:
        if event.meta.get(_ROUTE_MODE_META_KEY) != self.mode:
            return

        forwarded = event.model_copy(deep=True)
        forwarded.meta.pop(_ROUTE_MODE_META_KEY, None)
        await self.collector.handle_event(forwarded)


class FisheyeRuntime:
    def __init__(
        self,
        bus: AsyncEventBus,
        preprocessor_pipeline: PreprocessorPipeline | None = None,
        preprocessor_pipelines: dict[RouteMode, PreprocessorPipeline] | None = None,
        store: SQLiteStore | None = None,
    ) -> None:
        self.bus = bus
        if preprocessor_pipelines is not None:
            self.preprocessor_pipelines = preprocessor_pipelines
        elif preprocessor_pipeline is not None:
            self.preprocessor_pipelines = {"raw": preprocessor_pipeline}
        else:
            self.preprocessor_pipelines = {"raw": PreprocessorPipeline([])}

        self.store = store
        self.collectors: list[Collector] = []
        self.router = Router()
        self._started = False

    def register_collector(
        self,
        collector: Collector,
        event_types: tuple[str, ...] | None = None,
        max_retries: int | None = None,
        mode: RouteMode = "raw",
        route: Route | None = None,
    ) -> int:
        resolved_route = route or Route(
            collector_name=getattr(collector, "name", collector.__class__.__name__),
            mode=mode,
        )
        if resolved_route.mode not in self.preprocessor_pipelines:
            raise ValueError(f"No preprocessor pipeline configured for mode '{resolved_route.mode}'")

        patterns = event_types
        if patterns is None and resolved_route.event_pattern != "*":
            patterns = (resolved_route.event_pattern,)

        wrapped = _RoutedCollector(collector, resolved_route.mode)
        sub_id = self.bus.subscribe(wrapped, event_types=patterns, max_retries=max_retries)
        self.router.add_route(sub_id, resolved_route)
        self.collectors.append(collector)
        return sub_id

    async def start(self) -> None:
        if self._started:
            return
        await self.bus.start()
        self._started = True

    async def stop(self) -> None:
        if not self._started:
            return

        for mode in sorted(self._active_modes()):
            pipeline = self.preprocessor_pipelines.get(mode)
            if pipeline is None:
                continue

            flushed = await pipeline.flush()
            for event in flushed:
                event.meta[_ROUTE_MODE_META_KEY] = mode
                await self.bus.publish(event)

        await self.bus.drain(timeout=2.0)
        await self.bus.stop()
        self._started = False

    async def publish(self, event: EventEnvelope | dict[str, Any]) -> None:
        if not self._started:
            await self.start()

        normalized = event if isinstance(event, EventEnvelope) else EventEnvelope.model_validate(event)

        pipeline = self.preprocessor_pipelines.get("raw")
        if pipeline is None:
            return

        processed = await pipeline.process(normalized)
        for candidate in processed:
            candidate.meta[_ROUTE_MODE_META_KEY] = "raw"
            await self.bus.publish(candidate)

    async def ingest(self, events: list[EventEnvelope | dict[str, Any]]) -> None:
        for event in events:
            await self.publish(event)

    async def drain(self, timeout: float | None = None) -> None:
        await self.bus.drain(timeout=timeout)

    @property
    def metrics(self) -> dict[str, Any]:
        return self.bus.metrics

    def list_routes(self) -> dict[int, Route]:
        return self.router.list_routes()

    def _active_modes(self) -> set[RouteMode]:
        modes = self.router.active_modes()
        if modes:
            return modes
        return {"raw"}


def _build_embedding_provider(config: FisheyeConfig) -> LocalHashEmbeddingProvider | NoopEmbeddingProvider:
    provider = config.preprocessors.embedding_provider.lower()
    if provider == "local_hash":
        return LocalHashEmbeddingProvider(config.preprocessors.embedding_dimensions)
    if provider == "noop":
        return NoopEmbeddingProvider()
    raise ValueError(f"Unsupported embedding_provider: {provider}")


def _build_preprocessor_pipeline(
    config: FisheyeConfig,
    *,
    include_redaction: bool,
) -> PreprocessorPipeline:
    preprocessors: list[Preprocessor] = []

    if config.preprocessors.enable_buffering:
        preprocessors.append(
            BufferingPreprocessor(
                max_events=config.preprocessors.buffering_max_events,
                max_seconds=config.preprocessors.buffering_max_seconds,
            )
        )

    if include_redaction and config.preprocessors.enable_secret_redaction:
        preprocessors.append(SecretRedactionPreprocessor())
    if include_redaction and config.preprocessors.enable_pii_redaction:
        preprocessors.append(PIIRedactionPreprocessor())

    if config.preprocessors.enable_hashing:
        preprocessors.append(HashFingerprintPreprocessor())
    if config.preprocessors.enable_url_extraction:
        preprocessors.append(URLDomainExtractionPreprocessor())
    if config.preprocessors.enable_embeddings:
        preprocessors.append(EmbeddingPreprocessor(provider=_build_embedding_provider(config)))

    return PreprocessorPipeline(preprocessors)


def build_default_runtime(config: FisheyeConfig | None = None) -> FisheyeRuntime:
    config = config or FisheyeConfig()

    store = SQLiteStore(Path(config.storage.sqlite_path))
    logger = JsonlLoggerCollector(
        events_path=config.storage.events_jsonl_path,
        alerts_path=config.storage.alerts_jsonl_path,
    )

    detector_engine = DetectorEngine(
        detectors=[
            PromptInjectionDetector(),
            DataExfiltrationDetector(),
            DoSDetector(),
        ],
        store=store,
        alert_sinks=[logger],
        thresholds={
            "prompt_injection": config.thresholds.prompt_injection,
            "data_exfiltration": config.thresholds.data_exfiltration,
            "dos": config.thresholds.dos,
            "behavioral": config.thresholds.behavioral,
        },
        detector_weights=config.detector_weights,
    )

    behavior_monitor = StatisticalBehaviorMonitor(
        store=store,
        alert_sinks=[logger],
        threshold=config.thresholds.behavioral,
    )

    runtime = FisheyeRuntime(
        bus=AsyncEventBus(
            queue_size=config.bus.queue_size,
            default_retries=config.bus.retry_attempts,
        ),
        preprocessor_pipelines={
            "raw": _build_preprocessor_pipeline(config, include_redaction=False),
            "redacted": _build_preprocessor_pipeline(config, include_redaction=True),
        },
        store=store,
    )

    runtime.register_collector(store, mode="raw")
    runtime.register_collector(detector_engine, mode="raw")
    runtime.register_collector(behavior_monitor, mode="raw")
    runtime.register_collector(logger, mode="redacted")

    runtime.detector_engine = detector_engine  # type: ignore[attr-defined]
    runtime.behavior_monitor = behavior_monitor  # type: ignore[attr-defined]
    runtime.logger = logger  # type: ignore[attr-defined]
    runtime.config = config  # type: ignore[attr-defined]
    return runtime
