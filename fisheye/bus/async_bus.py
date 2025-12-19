from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import Any

from fisheye.collectors.base import Collector
from fisheye.schema.events import EventEnvelope


@dataclass(slots=True)
class _Subscription:
    collector: Collector
    queue: asyncio.Queue[EventEnvelope]
    event_types: tuple[str, ...] | None
    max_retries: int


class AsyncEventBus:
    def __init__(self, queue_size: int = 1000, default_retries: int = 1) -> None:
        self.queue_size = queue_size
        self.default_retries = default_retries
        self._subscriptions: dict[int, _Subscription] = {}
        self._workers: dict[int, asyncio.Task[None]] = {}
        self._next_id = 1
        self._running = False
        self._metrics: dict[str, int] = {
            "published": 0,
            "dropped": 0,
            "collector_errors": 0,
        }

    @staticmethod
    def _matches(event_type: str, patterns: tuple[str, ...] | None) -> bool:
        if patterns is None:
            return True
        for pattern in patterns:
            if pattern == event_type:
                return True
            if pattern.endswith("*") and event_type.startswith(pattern[:-1]):
                return True
        return False

    def subscribe(
        self,
        collector: Collector,
        event_types: tuple[str, ...] | None = None,
        max_retries: int | None = None,
    ) -> int:
        sub_id = self._next_id
        self._next_id += 1
        self._subscriptions[sub_id] = _Subscription(
            collector=collector,
            queue=asyncio.Queue(maxsize=self.queue_size),
            event_types=event_types,
            max_retries=max_retries if max_retries is not None else self.default_retries,
        )
        if self._running:
            self._workers[sub_id] = asyncio.create_task(self._worker(sub_id))
        return sub_id

    async def unsubscribe(self, sub_id: int) -> None:
        worker = self._workers.pop(sub_id, None)
        if worker:
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker
        self._subscriptions.pop(sub_id, None)

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        for sub_id in self._subscriptions:
            self._workers[sub_id] = asyncio.create_task(self._worker(sub_id))

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False

        for worker in self._workers.values():
            worker.cancel()
        for worker in self._workers.values():
            with contextlib.suppress(asyncio.CancelledError):
                await worker
        self._workers.clear()

    async def publish(self, event: EventEnvelope) -> None:
        self._metrics["published"] += 1
        for subscription in self._subscriptions.values():
            if not self._matches(event.event_type, subscription.event_types):
                continue
            try:
                subscription.queue.put_nowait(event)
            except asyncio.QueueFull:
                self._metrics["dropped"] += 1

    async def drain(self, timeout: float | None = None) -> None:
        joins = [subscription.queue.join() for subscription in self._subscriptions.values()]
        if not joins:
            return
        if timeout is None:
            await asyncio.gather(*joins)
            return
        await asyncio.wait_for(asyncio.gather(*joins), timeout=timeout)

    async def _worker(self, sub_id: int) -> None:
        subscription = self._subscriptions[sub_id]
        while True:
            event = await subscription.queue.get()
            try:
                attempt = 0
                while True:
                    try:
                        await subscription.collector.handle_event(event)
                        break
                    except Exception:
                        attempt += 1
                        if attempt > subscription.max_retries:
                            self._metrics["collector_errors"] += 1
                            break
                        await asyncio.sleep(min(0.1 * attempt, 0.5))
            finally:
                subscription.queue.task_done()

    @property
    def metrics(self) -> dict[str, Any]:
        return dict(self._metrics)
