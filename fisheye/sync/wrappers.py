from __future__ import annotations

import asyncio
from concurrent.futures import Future
from threading import Event, Thread, current_thread
from typing import Any, Coroutine

from fisheye.runtime import FisheyeRuntime
from fisheye.schema.events import EventEnvelope


class SyncFisheyeRuntime:
    """Own a persistent event loop for synchronous producers and callbacks."""

    def __init__(self, runtime: FisheyeRuntime) -> None:
        self.runtime = runtime
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: Thread | None = None

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is not None:
            return self._loop
        ready = Event()

        def run() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            ready.set()
            try:
                loop.run_forever()
            finally:
                loop.run_until_complete(loop.shutdown_asyncgens())
                loop.run_until_complete(loop.shutdown_default_executor())
                loop.close()

        self._thread = Thread(target=run, name="fisheye-runtime", daemon=True)
        self._thread.start()
        ready.wait()
        assert self._loop is not None
        return self._loop

    def submit(self, coroutine: Coroutine[Any, Any, Any]) -> Future[Any]:
        return asyncio.run_coroutine_threadsafe(coroutine, self._ensure_loop())

    def _call(self, coroutine: Coroutine[Any, Any, Any]) -> Any:
        if current_thread() is self._thread:
            coroutine.close()
            raise RuntimeError("Use the async runtime inside its event loop")
        return self.submit(coroutine).result()

    def start(self) -> None:
        self._call(self.runtime.start())

    def stop(self) -> None:
        if self._loop is None:
            return
        try:
            self._call(self.runtime.stop())
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            assert self._thread is not None
            self._thread.join()
            self._loop = None
            self._thread = None

    def close(self) -> None:
        try:
            self.stop()
        finally:
            if self.runtime.store:
                self.runtime.store.close()

    def publish(self, event: EventEnvelope | dict[str, Any]) -> Any:
        return self._call(self.runtime.publish(event))

    def ingest(self, events: list[EventEnvelope | dict[str, Any]]) -> Any:
        return self._call(self.runtime.ingest(events))

    def drain(self, timeout: float | None = None) -> None:
        self._call(self.runtime.drain(timeout))

    def __enter__(self) -> "SyncFisheyeRuntime":
        self.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
