from __future__ import annotations

from typing import Any

from fisheye.adapters.base import AdapterBase


class LangChainAdapter(AdapterBase):
    framework = "langchain"

    async def on_chain_start(self, serialized: dict[str, Any], inputs: dict[str, Any]) -> None:
        await self.emit("state.update", {"serialized": serialized, "inputs": inputs, "stage": "chain_start"})

    async def on_chain_end(self, outputs: dict[str, Any]) -> None:
        await self.emit("state.update", {"outputs": outputs, "stage": "chain_end"})

    async def on_llm_start(self, prompts: list[str], extra: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"prompts": prompts}
        if extra:
            payload.update(extra)
        await self.emit("llm.request", payload)

    async def on_llm_end(self, generations: Any, extra: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"generations": generations}
        if extra:
            payload.update(extra)
        await self.emit("llm.response", payload)

    async def on_tool_start(self, name: str, input_str: str, extra: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"tool_name": name, "input": input_str}
        if extra:
            payload.update(extra)
        await self.emit("tool.call.start", payload)

    async def on_tool_end(self, name: str, output: str, extra: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"tool_name": name, "output": output}
        if extra:
            payload.update(extra)
        await self.emit("tool.call.end", payload)

    def as_callback_handler(self) -> "LangChainCallbackHandler":
        return LangChainCallbackHandler(self)


class LangChainCallbackHandler:
    """Dependency-free callback shim matching common LangChain callback names."""

    def __init__(self, adapter: LangChainAdapter) -> None:
        self.adapter = adapter

    def on_chain_start(
        self,
        serialized: dict[str, Any],
        inputs: dict[str, Any],
        **_: Any,
    ) -> None:
        self.adapter.dispatch(self.adapter.on_chain_start(serialized, inputs))

    def on_chain_end(self, outputs: dict[str, Any], **_: Any) -> None:
        self.adapter.dispatch(self.adapter.on_chain_end(outputs))

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        **kwargs: Any,
    ) -> None:
        extra = {"serialized": serialized}
        if kwargs:
            extra.update(kwargs)
        self.adapter.dispatch(self.adapter.on_llm_start(prompts, extra=extra))

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        extra = kwargs if kwargs else None
        self.adapter.dispatch(self.adapter.on_llm_end(response, extra=extra))

    def on_tool_start(self, serialized: dict[str, Any], input_str: str, **kwargs: Any) -> None:
        name = str(serialized.get("name") or serialized.get("id") or "unknown_tool")
        extra = kwargs if kwargs else None
        self.adapter.dispatch(self.adapter.on_tool_start(name=name, input_str=input_str, extra=extra))

    def on_tool_end(self, output: str, **kwargs: Any) -> None:
        name = str(kwargs.get("name") or kwargs.get("tool_name") or "unknown_tool")
        extra = {k: v for k, v in kwargs.items() if k not in {"name", "tool_name"}}
        self.adapter.dispatch(self.adapter.on_tool_end(name=name, output=output, extra=extra or None))

    def on_tool_error(self, error: BaseException, **kwargs: Any) -> None:
        name = str(kwargs.get("name") or kwargs.get("tool_name") or "unknown_tool")
        payload: dict[str, Any] = {"tool_name": name, "error": str(error)}
        if kwargs:
            payload.update(kwargs)
        self.adapter.dispatch(self.adapter.emit("tool.call.error", payload))
