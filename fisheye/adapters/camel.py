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
