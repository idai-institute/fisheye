from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any
import asyncio
import time
from uuid import uuid4

from fisheye.adapters.base import AdapterBase


class GenericAdapter(AdapterBase):
    framework = "generic"

    async def on_agent_start(self, payload: dict[str, Any] | None = None) -> None:
        await self.emit("agent.start", payload or {})

    async def on_agent_stop(self, payload: dict[str, Any] | None = None) -> None:
        await self.emit("agent.stop", payload or {})

    async def on_agent_error(self, error: str, payload: dict[str, Any] | None = None) -> None:
        merged = {"error": error}
        if payload:
            merged.update(payload)
        await self.emit("agent.error", merged)

    async def on_llm_request(self, message: str, payload: dict[str, Any] | None = None) -> None:
        merged = {"message": message}
        if payload:
            merged.update(payload)
        await self.emit("llm.request", merged)

    async def on_llm_response(self, message: str, payload: dict[str, Any] | None = None) -> None:
        merged = {"message": message}
        if payload:
            merged.update(payload)
        await self.emit("llm.response", merged)

    async def on_tool_call_start(self, tool_name: str, payload: dict[str, Any] | None = None) -> None:
        merged = {"tool_name": tool_name}
        if payload:
            merged.update(payload)
        await self.emit("tool.call.start", merged)

    async def on_tool_call_end(self, tool_name: str, payload: dict[str, Any] | None = None) -> None:
        merged = {"tool_name": tool_name}
        if payload:
            merged.update(payload)
        await self.emit("tool.call.end", merged)

    def wrap_tool(self, tool_name: str, func: Callable[..., Any]) -> Callable[..., Any]:
        if hasattr(func, "__call__"):
            @wraps(func)
            async def wrapper(*args: Any, **kwargs: Any) -> Any:
                call_id = uuid4().hex
                started = time.perf_counter()
                first = await self.emit("tool.call.start", {"tool_name": tool_name, "call_id": call_id,
                                                            "arguments": {"args": list(args), "kwargs": kwargs}})
                try:
                    result = await self._run_callable(func, *args, **kwargs)
                except (Exception, asyncio.CancelledError) as exc:
                    await self.emit(
                        "tool.call.error",
                        {"tool_name": tool_name, "call_id": call_id, "error_type": type(exc).__name__},
                        links=[first.event_id],
                    )
                    raise
                await self.emit("tool.call.end", {"tool_name": tool_name, "call_id": call_id, "output": result,
                                                  "latency_ms": (time.perf_counter() - started) * 1000}, links=[first.event_id])
                return result

            return wrapper
        raise TypeError("func must be callable")
