from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from fisheye.runtime import FisheyeRuntime
from fisheye.schema.events import EventEnvelope


class AdapterBase:
    framework: str = "generic"

    def __init__(self, runtime: FisheyeRuntime, agent_id: str, run_id: str) -> None:
        self.runtime = runtime
        self.agent_id = agent_id
        self.run_id = run_id

    async def emit(self, event_type: str, payload: dict[str, Any], **kwargs: Any) -> EventEnvelope:
        event = EventEnvelope(
            event_type=event_type,
            payload=payload,
            agent_id=self.agent_id,
            run_id=self.run_id,
            framework=self.framework,
            **kwargs,
        )
        await self.runtime.publish(event)
        return event

    def dispatch(self, coroutine: Awaitable[Any]) -> None:
        """Submit callbacks to the runtime's owned loop when available."""
        if hasattr(self.runtime, "submit"):
            self.runtime.submit(coroutine)
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(coroutine)
            return
        loop.create_task(coroutine)

    @staticmethod
    async def _run_callable(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        if inspect.iscoroutinefunction(func):
            return await func(*args, **kwargs)
        return await asyncio.to_thread(func, *args, **kwargs)
