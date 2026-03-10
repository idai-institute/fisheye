from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Coroutine

from fisheye.behavior.monitor import StatisticalBehaviorMonitor
from fisheye.bus.async_bus import AsyncEventBus, DeliveryReceipt
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
from fisheye.preprocessors.features import FeatureExtractionPreprocessor, FeatureOnlyProjectionPreprocessor
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
        self._loop: asyncio.AbstractEventLoop | None = None
        self._pending: set[asyncio.Task[Any]] = set()
        self._sync_bridge: Any = None
        self._flush_task: asyncio.Task[None] | None = None

    def submit(self, coroutine: Coroutine[Any, Any, Any]) -> Any:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if self._loop and self._loop.is_running() and loop is not self._loop:
            return asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        if loop is not None:
            task = loop.create_task(coroutine)
            self._pending.add(task)
            return task
        if self._sync_bridge is None:
            from fisheye.sync.wrappers import SyncFisheyeRuntime
            self._sync_bridge = SyncFisheyeRuntime(self)
        return self._sync_bridge.submit(coroutine).result()

    def close(self) -> None:
        if self._sync_bridge is not None:
            self._sync_bridge.close()
        elif self._started:
            raise RuntimeError("Use 'await aclose()' for an asynchronous runtime")
        elif self.store:
            self.store.close()

    async def aclose(self) -> None:
        try:
            await self.stop()
        finally:
            if self.store:
                self.store.close()

    async def __aenter__(self) -> "FisheyeRuntime":
        await self.start()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()

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
        self._loop = asyncio.get_running_loop()
        await self.bus.start()
        self._started = True
        self._flush_task = asyncio.create_task(self._flush_periodically())

    async def _flush_periodically(self) -> None:
        while True:
            await asyncio.sleep(0.1)
            for mode, pipeline in self.preprocessor_pipelines.items():
                for processor in pipeline.preprocessors:
                    if isinstance(processor, BufferingPreprocessor) and processor._should_flush():
                        await self._flush_mode(mode)
                        break

    async def _publish_mode(self, event: EventEnvelope, mode: RouteMode) -> DeliveryReceipt:
        event.meta[_ROUTE_MODE_META_KEY] = mode
        ids = [i for i, route in self.router.list_routes().items() if route.mode == mode]
        return await self.bus.publish(event, subscription_ids=ids)

    async def _flush_mode(self, mode: RouteMode) -> None:
        for event in await self.preprocessor_pipelines[mode].flush():
            await self._publish_mode(event, mode)

    async def stop(self) -> None:
        if not self._started:
            return

        if self._flush_task:
            self._flush_task.cancel()
            await asyncio.gather(self._flush_task, return_exceptions=True)
            self._flush_task = None

        for mode in sorted(self._active_modes()):
            pipeline = self.preprocessor_pipelines.get(mode)
            if pipeline is None:
                continue

            flushed = await pipeline.flush()
            for event in flushed:
                await self._publish_mode(event, mode)

        try:
            await self.drain(timeout=10.0)
        finally:
            await self.bus.stop()
            self._started = False

    async def publish(self, event: EventEnvelope | dict[str, Any]) -> DeliveryReceipt:
        if not self._started:
            await self.start()

        normalized = event if isinstance(event, EventEnvelope) else EventEnvelope.model_validate(event)

        delivered = dropped = 0
        for mode in sorted(self._active_modes()):
            pipeline = self.preprocessor_pipelines.get(mode)
            if pipeline is None:
                continue

            seed = normalized.model_copy(deep=True)
            processed = await pipeline.process(seed)
            for candidate in processed:
                receipt = await self._publish_mode(candidate, mode)
                delivered += receipt.delivered
                dropped += receipt.dropped
        return DeliveryReceipt(normalized.event_id, delivered, dropped)

    async def ingest(self, events: list[EventEnvelope | dict[str, Any]]) -> None:
        for event in events:
            await self.publish(event)

    async def drain(self, timeout: float | None = None) -> None:
        pending = self._pending - {asyncio.current_task()}
        if pending:
            await asyncio.wait_for(asyncio.gather(*pending), timeout)
            self._pending.difference_update(pending)
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
        return set(self.preprocessor_pipelines)


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
    feature_only: bool,
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
    if config.preprocessors.enable_features:
        preprocessors.append(FeatureExtractionPreprocessor())
    if config.preprocessors.enable_url_extraction:
        preprocessors.append(URLDomainExtractionPreprocessor())
    if config.preprocessors.enable_embeddings:
        preprocessors.append(EmbeddingPreprocessor(provider=_build_embedding_provider(config)))

    if feature_only:
        preprocessors.append(FeatureOnlyProjectionPreprocessor())

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
            "raw": _build_preprocessor_pipeline(config, include_redaction=False, feature_only=False),
            "redacted": _build_preprocessor_pipeline(config, include_redaction=True, feature_only=False),
            "feature_only": _build_preprocessor_pipeline(config, include_redaction=True, feature_only=True),
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
