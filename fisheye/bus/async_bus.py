from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from typing import Any, Literal

from fisheye.collectors.base import Collector
from fisheye.schema.events import EventEnvelope


class OverloadedError(RuntimeError):
    """The event was not admitted; the caller may retry the same event ID."""


@dataclass(frozen=True)
class DeliveryReceipt:
    event_id: str
    delivered: int
    dropped: int = 0
    durable: bool = False
    duplicate: bool = False
    sequence: int | None = None


@dataclass
class _Subscription:
    collector: Collector
    queue: asyncio.Queue[EventEnvelope]
    event_types: tuple[str, ...] | None
    max_retries: int
    metrics: dict[str, int] = field(default_factory=lambda: dict(delivered=0, processed=0, dropped=0, errors=0, retries=0))


class AsyncEventBus:
    def __init__(self, queue_size: int = 1000, default_retries: int = 1,
                 overflow: Literal['reject', 'wait', 'drop'] = 'reject',
                 delivery_timeout: float = 5.0) -> None:
        if queue_size < 1 or default_retries < 0 or overflow not in {'reject', 'wait', 'drop'}:
            raise ValueError('Invalid queue size, retry count, or overflow policy')
        self.queue_size, self.default_retries = queue_size, default_retries
        self.overflow, self.delivery_timeout = overflow, delivery_timeout
        self._subscriptions: dict[int, _Subscription] = {}
        self._workers: dict[int, asyncio.Task[None]] = {}
        self._next_id, self._running = 1, False
        self._metrics = dict(published=0, dropped=0, collector_errors=0, rejected=0)
        self.dead_letters: list[dict[str, Any]] = []

    @staticmethod
    def _matches(event_type: str, patterns: tuple[str, ...] | None) -> bool:
        return patterns is None or any(p == event_type or (p.endswith('*') and event_type.startswith(p[:-1])) for p in patterns)

    def subscribe(self, collector: Collector, event_types: tuple[str, ...] | None = None,
                  max_retries: int | None = None) -> int:
        sub_id = self._next_id
        self._next_id += 1
        self._subscriptions[sub_id] = _Subscription(collector, asyncio.Queue(self.queue_size), event_types,
                                                  self.default_retries if max_retries is None else max_retries)
        if self._running:
            self._workers[sub_id] = asyncio.create_task(self._worker(sub_id))
        return sub_id

    async def unsubscribe(self, sub_id: int) -> None:
        task = self._workers.pop(sub_id, None)
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._subscriptions.pop(sub_id, None)

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._workers = {key: asyncio.create_task(self._worker(key)) for key in self._subscriptions}

    async def stop(self) -> None:
        self._running = False
        for task in self._workers.values():
            task.cancel()
        await asyncio.gather(*self._workers.values(), return_exceptions=True)
        self._workers.clear()

    async def publish(self, event: EventEnvelope, subscription_ids: list[int] | None = None) -> DeliveryReceipt:
        ids = self._subscriptions if subscription_ids is None else subscription_ids
        targets = [self._subscriptions[i] for i in ids if self._matches(event.event_type, self._subscriptions[i].event_types)]
        if self.overflow == 'reject' and any(s.queue.full() for s in targets):
            self._metrics['rejected'] += 1
            raise OverloadedError('A collector queue is full')
        delivered = dropped = 0
        for sub in targets:
            if self.overflow == 'wait':
                try:
                    await asyncio.wait_for(sub.queue.put(event.model_copy(deep=True)), self.delivery_timeout)
                except asyncio.TimeoutError as exc:
                    # May be partially delivered; receipt-aware consumers must be idempotent.
                    self._metrics['rejected'] += 1
                    raise OverloadedError('Timed out waiting for a collector') from exc
            else:
                try:
                    sub.queue.put_nowait(event.model_copy(deep=True))
                except asyncio.QueueFull:
                    dropped += 1
                    sub.metrics['dropped'] += 1
                    continue
            sub.metrics['delivered'] += 1
            delivered += 1
        self._metrics['published'] += 1
        self._metrics['dropped'] += dropped
        return DeliveryReceipt(event.event_id, delivered, dropped)

    async def drain(self, timeout: float | None = None) -> None:
        await asyncio.wait_for(asyncio.gather(*(s.queue.join() for s in self._subscriptions.values())), timeout)

    async def _worker(self, sub_id: int) -> None:
        sub = self._subscriptions[sub_id]
        while True:
            event = await sub.queue.get()
            try:
                for attempt in range(sub.max_retries + 1):
                    try:
                        await asyncio.wait_for(sub.collector.handle_event(event), self.delivery_timeout)
                        sub.metrics['processed'] += 1
                        break
                    except Exception as exc:
                        if attempt == sub.max_retries:
                            sub.metrics['errors'] += 1
                            self._metrics['collector_errors'] += 1
                            self.dead_letters.append(dict(event_id=event.event_id, collector=sub.collector.name,
                                                          error_type=type(exc).__name__))
                            self.dead_letters = self.dead_letters[-1000:]
                        else:
                            sub.metrics['retries'] += 1
                            await asyncio.sleep(min(0.1 * (attempt + 1), 1))
            finally:
                sub.queue.task_done()

    @property
    def metrics(self) -> dict[str, Any]:
        return {**self._metrics, 'consumers': {str(i): dict(s.metrics, name=s.collector.name, queued=s.queue.qsize())
                                              for i, s in self._subscriptions.items()}}
