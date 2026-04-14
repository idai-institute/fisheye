from __future__ import annotations

import time
from collections.abc import Callable
from functools import wraps
from typing import Any

from fisheye.adapters.base import AdapterBase


class OpenAIAgentsAdapter(AdapterBase):
    framework = "openai_agents"
    capabilities = {'observe': True, 'native_hooks': True, 'pre_action': False, 'pause_resume': False}

    def as_run_hooks(self):
        return native_run_hooks(self)

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


def native_run_hooks(adapter):
    """Return real RunHooks; install fisheye[openai-agents] to use this bridge."""
    from agents import RunHooks
    from contextvars import ContextVar
    from uuid import uuid4
    from fisheye.adapters.serialization import json_safe
    call=ContextVar('fisheye_openai_call',default=None)

    class Hooks(RunHooks):
        async def emit_for(self,agent,kind,payload,**kwargs):
            from fisheye.schema.events import EventEnvelope
            event=EventEnvelope(schema_version='2',event_type=kind,agent_id=agent.name,
                run_id=adapter.run_id,workflow_id=adapter.run_id,framework='openai_agents',payload=json_safe(payload),**kwargs)
            await adapter.runtime.publish(event)
            return event

        async def on_agent_start(self,context,agent):
            await self.emit_for(agent,'agent.start',{})

        async def on_agent_end(self,context,agent,output):
            await self.emit_for(agent,'llm.response',dict(output=output,token_count=context.usage.total_tokens))
            await self.emit_for(agent,'agent.stop',{})

        async def on_handoff(self,context,from_agent,to_agent):
            await self.emit_for(from_agent,'task.delegated',dict(task_id=uuid4().hex,recipient_id=to_agent.name))

        async def on_tool_start(self,context,agent,tool):
            first=await self.emit_for(agent,'tool.call.start',dict(tool_name=tool.name))
            call.set((first.event_id,time.perf_counter()))

        async def on_tool_end(self,context,agent,tool,result):
            previous=call.get()
            await self.emit_for(agent,'tool.call.end',dict(tool_name=tool.name,output=result,
                latency_ms=(time.perf_counter()-previous[1])*1000 if previous else None),
                links=[previous[0]] if previous else [])
            call.set(None)
    return Hooks()
