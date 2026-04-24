from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any

from fisheye.adapters.base import AdapterBase


class CamelAdapter(AdapterBase):
    framework = "camel"

    async def on_agent_message(self, role: str, content: str, extra: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"role": role, "content": content}
        if extra:
            payload.update(extra)
        await self.emit("llm.message", payload)

    async def on_tool_invocation(self, tool_name: str, args: dict[str, Any]) -> None:
        await self.emit("tool.call.start", {"tool_name": tool_name, "arguments": args})

    async def on_tool_result(self, tool_name: str, output: Any) -> None:
        await self.emit("tool.call.end", {"tool_name": tool_name, "output": output})

    async def on_failure(self, error: str, extra: dict[str, Any] | None = None) -> None:
        payload = {"error": error}
        if extra:
            payload.update(extra)
        await self.emit("agent.error", payload)

    def wrap_tool(self, tool_name: str, func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            await self.on_tool_invocation(tool_name, {"args": args, "kwargs": kwargs})
            try:
                result = await self._run_callable(func, *args, **kwargs)
            except Exception as exc:
                await self.emit("tool.call.error", {"tool_name": tool_name, "error": str(exc)})
                raise
            await self.on_tool_result(tool_name, result)
            return result

        return wrapper

    def wrap_message_handler(self, role: str, func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            result = await self._run_callable(func, *args, **kwargs)
            await self.on_agent_message(role=role, content=str(result))
            return result

        return wrapper

    def wrap_agent(self, agent):
        """Explicit ChatAgent proxy; no global hooks or monkeypatching."""
        from fisheye.adapters.serialization import json_safe

        adapter = self

        class ObservedAgent:
            def __getattr__(self, name):
                return getattr(agent, name)

            async def astep(self, input_message, *args, **kwargs):
                await adapter.emit("agent.start", {})
                request = await adapter.emit(
                    "llm.request", json_safe({"content": getattr(input_message, "content", input_message)})
                )
                try:
                    result = await agent.astep(input_message, *args, **kwargs)
                except BaseException as exc:
                    await adapter.emit("agent.error", {"error_type": type(exc).__name__}, links=[request.event_id])
                    raise
                await adapter.emit(
                    "llm.response",
                    json_safe({"messages": [getattr(m, "content", "") for m in result.msgs], "info": result.info}),
                    links=[request.event_id],
                )
                await adapter.emit("agent.stop", {"terminated": result.terminated})
                return result

            def step(self, input_message, *args, **kwargs):
                import asyncio

                try:
                    asyncio.get_running_loop()
                except RuntimeError:
                    result = adapter.runtime.submit(self.astep(input_message, *args, **kwargs))
                    return result.result() if hasattr(result, "result") else result
                raise RuntimeError("Use astep inside an event loop")

        return ObservedAgent()

    def as_function_tool(self, func, name=None):
        from camel.toolkits import FunctionTool

        from fisheye.adapters.generic import GenericAdapter

        return FunctionTool(GenericAdapter.wrap_tool(self, name or func.__name__, func))
