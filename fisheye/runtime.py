from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, Coroutine

from fisheye.analysis import AnalysisProcessor
from fisheye.bus.async_bus import AsyncEventBus, DeliveryReceipt
from fisheye.bus.routing import Route, RouteMode, Router
from fisheye.collectors.base import Collector
from fisheye.collectors.journal import JournalStore
from fisheye.collectors.jsonl_logger import JsonlLoggerCollector
from fisheye.collectors.sqlite_store import SQLiteStore
from fisheye.config import FisheyeConfig
from fisheye.preprocessors.base import Preprocessor
from fisheye.preprocessors.buffering import BufferingPreprocessor
from fisheye.preprocessors.embeddings import EmbeddingPreprocessor, LocalHashEmbeddingProvider, NoopEmbeddingProvider
from fisheye.preprocessors.features import FeatureExtractionPreprocessor, FeatureOnlyProjectionPreprocessor
from fisheye.preprocessors.hashing import HashFingerprintPreprocessor
from fisheye.preprocessors.pipeline import PreprocessorPipeline
from fisheye.preprocessors.redaction import PIIRedactionPreprocessor, SecretRedactionPreprocessor
from fisheye.preprocessors.urls import URLDomainExtractionPreprocessor
from fisheye.privacy import capture_event
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
    def workflow(self, workflow_id: str | None = None, **kwargs: Any):
        from fisheye.context import Workflow

        return Workflow(self, workflow_id, **kwargs)

    def sync(self):
        from fisheye.sync.wrappers import SyncFisheyeRuntime

        return SyncFisheyeRuntime(self)

    def supervise(self, policy, tools=None):
        from fisheye.policies import Supervisor

        if not isinstance(self.store, JournalStore):
            raise ValueError("Supervision requires a durable store")
        self.supervisor = Supervisor(self.store, policy, tools, runtime=self)
        return self.supervisor

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
        self.analysis: AnalysisProcessor | None = None
        self._journal_task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._analysis_error: str | None = None
        self._journal_metrics: dict[str, Any] = {}
        self._export_task: asyncio.Task[None] | None = None
        self._export_error: str | None = None
        self._callback_errors: list[str] = []
        self._last_maintenance = time.monotonic()

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

            def completed(task):
                self._pending.discard(task)
                if not task.cancelled() and task.exception():
                    self._callback_errors.append(type(task.exception()).__name__)
                    self._callback_errors[:] = self._callback_errors[-100:]

            task.add_done_callback(completed)
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
        if self.analysis is not None and isinstance(self.store, JournalStore):
            await self.store.prune(self.config.storage.retention_days, self.config.storage.state_ttl_seconds)
            self._journal_task = asyncio.create_task(self._process_journal())
            self._export_task = asyncio.create_task(self._export_journal())
            self._wake.set()

    async def _process_journal(self) -> None:
        assert isinstance(self.store, JournalStore) and self.analysis is not None
        while True:
            self._wake.clear()
            try:
                pending = await self.store.pending()
                for sequence, event in pending:
                    result = await self.analysis.analyze(event, await self.store.checkpoint(event.scope))
                    await self.store.commit_analysis(
                        sequence, event, result.state, result.alerts, result.findings, result.signals, result.errors
                    )
                    for mode in sorted(self._active_modes()):
                        for projected in await self.preprocessor_pipelines[mode].process(event.model_copy(deep=True)):
                            await self._publish_mode(projected, mode)
                self._analysis_error = None
                self._journal_metrics = await self.store.journal_metrics()
                if pending:
                    continue
            except Exception as exc:
                self._analysis_error = type(exc).__name__
                await asyncio.sleep(0.1)
                continue
            await self._wake.wait()

    async def _export_journal(self) -> None:
        import json

        from fisheye.schema.alerts import Alert

        while True:
            try:
                items = await self.store.pending_exports()
                for item in items:
                    data = json.loads(item["data_json"])
                    if item["kind"] == "event":
                        await self.logger.handle_event(EventEnvelope.model_validate(data))
                    else:
                        await self.logger.handle_alert(Alert.model_validate(data))
                    await self.store.acknowledge_export(item["id"])
                self._export_error = None
                if items:
                    continue
            except Exception as exc:
                self._export_error = type(exc).__name__
            await asyncio.sleep(0.05)

    async def _flush_periodically(self) -> None:
        while True:
            await asyncio.sleep(0.1)
            if self.analysis is not None and time.monotonic() - self._last_maintenance > 60:
                await self.store.prune(self.config.storage.retention_days, self.config.storage.state_ttl_seconds)
                self._last_maintenance = time.monotonic()
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
            if self._journal_task:
                self._journal_task.cancel()
                await asyncio.gather(self._journal_task, return_exceptions=True)
                self._journal_task = None
            if self._export_task:
                self._export_task.cancel()
                await asyncio.gather(self._export_task, return_exceptions=True)
                self._export_task = None
            await self.bus.stop()
            self._started = False

    async def publish(self, event: EventEnvelope | dict[str, Any]) -> DeliveryReceipt:
        if not self._started:
            await self.start()

        normalized = event if isinstance(event, EventEnvelope) else EventEnvelope.model_validate(event)

        if self.analysis is not None and isinstance(self.store, JournalStore):
            identity_event = normalized
            normalized = capture_event(
                normalized,
                raw=self.config.storage.capture == "raw",
                feature_only=self.config.storage.capture == "features",
            )
            receipt = await self.store.accept(normalized, identity_event=identity_event)
            self._wake.set()
            return receipt

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
        if self._callback_errors:
            errors, self._callback_errors = self._callback_errors, []
            raise RuntimeError("Callback delivery failed: " + ", ".join(errors))
        pending = self._pending - {asyncio.current_task()}
        if pending:
            await asyncio.wait_for(asyncio.gather(*pending), timeout)
            self._pending.difference_update(pending)
        if self.analysis is not None and isinstance(self.store, JournalStore):

            async def finish_journal() -> None:
                while (await self.store.journal_metrics())["pending"]:
                    if self._analysis_error:
                        raise RuntimeError(f"Analysis unavailable: {self._analysis_error}")
                    self._wake.set()
                    await asyncio.sleep(0.005)

            await asyncio.wait_for(finish_journal(), timeout)

            async def finish_exports():
                while await self.store.pending_exports(1):
                    if self._export_error:
                        raise RuntimeError("Export unavailable: " + self._export_error)
                    await asyncio.sleep(0.005)

            await asyncio.wait_for(finish_exports(), timeout)
        for mode in sorted(self._active_modes()):
            await self._flush_mode(mode)
        await self.bus.drain(timeout=timeout)

    @property
    def metrics(self) -> dict[str, Any]:
        return dict(
            self.bus.metrics,
            journal=self._journal_metrics,
            analysis_error=self._analysis_error,
            export_error=self._export_error,
            coverage=self.analysis.coverage if self.analysis else {},
        )

    def list_routes(self) -> dict[int, Route]:
        return self.router.list_routes()

    def _active_modes(self) -> set[RouteMode]:
        modes = self.router.active_modes()
        if modes or self.analysis is not None:
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
    store = JournalStore(Path(config.storage.sqlite_path), max_pending=config.storage.max_pending)
    store.raw_capture = config.storage.capture == "raw"
    logger = JsonlLoggerCollector(
        config.storage.events_jsonl_path,
        config.storage.alerts_jsonl_path,
        redact_content=config.storage.capture != "raw",
    )
    analysis = AnalysisProcessor(
        thresholds={
            "prompt_injection": config.thresholds.prompt_injection,
            "data_exfiltration": config.thresholds.data_exfiltration,
            "dos": config.thresholds.dos,
            "behavioral": config.thresholds.behavioral,
        },
        detector_weights=config.detector_weights,
        config_version=config.fingerprint,
        min_samples=config.oversight.baseline_min_samples,
        frozen=config.oversight.baseline_frozen,
    )
    from fisheye.graph import WorkflowGraph

    analysis.graph = WorkflowGraph(
        config.oversight.graph_max_events,
        config.oversight.token_budget,
        config.oversight.cost_budget,
        config.oversight.call_budget,
    )
    runtime = FisheyeRuntime(
        bus=AsyncEventBus(queue_size=config.bus.queue_size, default_retries=config.bus.retry_attempts, overflow="drop"),
        preprocessor_pipelines={
            "raw": _build_preprocessor_pipeline(config, include_redaction=False, feature_only=False),
            "redacted": _build_preprocessor_pipeline(config, include_redaction=True, feature_only=False),
            "feature_only": _build_preprocessor_pipeline(config, include_redaction=True, feature_only=True),
        },
        store=store,
    )
    runtime.analysis = analysis
    runtime.detector_engine = analysis
    runtime.logger = logger
    runtime.config = config
    return runtime
