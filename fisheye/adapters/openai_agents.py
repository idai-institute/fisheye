from __future__ import annotations

import time
from collections.abc import Callable
from functools import wraps
from typing import Any

from fisheye.adapters.base import AdapterBase


class OpenAIAgentsAdapter(AdapterBase):
    framework = "openai_agents"

    async def on_run_started(self, payload: dict[str, Any] | None = None) -> None:
        await self.emit("agent.start", payload or {})

    async def on_run_finished(self, payload: dict[str, Any] | None = None) -> None:
        await self.emit("agent.stop", payload or {})

    async def on_message(self, content: str, role: str = "assistant", extra: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"content": content, "role": role}
        if extra:
            payload.update(extra)
        await self.emit("llm.message", payload)

    async def on_tool_call(self, tool_name: str, args: dict[str, Any]) -> None:
        await self.emit("tool.call.start", {"tool_name": tool_name, "arguments": args})

    async def on_tool_result(self, tool_name: str, output: Any, latency_ms: float | None = None) -> None:
        payload: dict[str, Any] = {"tool_name": tool_name, "output": output}
        if latency_ms is not None:
            payload["latency_ms"] = latency_ms
        await self.emit("tool.call.end", payload)

    def wrap_tool(self, tool_name: str, func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            await self.on_tool_call(tool_name, {"args": args, "kwargs": kwargs})
            started_at = time.perf_counter()
            try:
                result = await self._run_callable(func, *args, **kwargs)
            except Exception as exc:
                await self.emit("tool.call.error", {"tool_name": tool_name, "error": str(exc)})
                raise
            latency_ms = (time.perf_counter() - started_at) * 1000.0
            await self.on_tool_result(tool_name, result, latency_ms=latency_ms)
            return result

        return wrapper

    def as_event_hook(self) -> "OpenAIAgentsEventHook":
        return OpenAIAgentsEventHook(self)


class OpenAIAgentsEventHook:
    """Event hook for frameworks exposing an `on_event` callback pipeline."""

    def __init__(self, adapter: OpenAIAgentsAdapter) -> None:
        self.adapter = adapter

    def on_event(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("type") or "")
        payload = dict(event.get("payload") or {})

        if event_type == "run.started":
            self.adapter.dispatch(self.adapter.on_run_started(payload))
            return
        if event_type == "run.finished":
            self.adapter.dispatch(self.adapter.on_run_finished(payload))
            return
        if event_type == "message":
            content = str(payload.get("content") or "")
            role = str(payload.get("role") or "assistant")
            extra = {k: v for k, v in payload.items() if k not in {"content", "role"}}
            self.adapter.dispatch(self.adapter.on_message(content, role=role, extra=extra or None))
            return
        if event_type == "tool.start":
            tool_name = str(payload.get("tool_name") or payload.get("name") or "unknown_tool")
            args = payload.get("arguments") if isinstance(payload.get("arguments"), dict) else payload
            self.adapter.dispatch(self.adapter.on_tool_call(tool_name, args))
            return
        if event_type == "tool.end":
            tool_name = str(payload.get("tool_name") or payload.get("name") or "unknown_tool")
            output = payload.get("output")
            latency_ms = payload.get("latency_ms")
            self.adapter.dispatch(self.adapter.on_tool_result(tool_name, output, latency_ms=latency_ms))
            return
        # Unknown event shape; still persist for visibility.
        self.adapter.dispatch(self.adapter.emit("custom.openai_agents", payload))
