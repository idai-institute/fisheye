from __future__ import annotations

from fisheye.adapters.base import AdapterBase
from fisheye.adapters.serialization import json_safe

try:
    from langchain_core.callbacks import BaseCallbackHandler
except ImportError:

    class BaseCallbackHandler:
        """Callback shape remains usable without the optional framework."""

        pass


class LangChainAdapter(AdapterBase):
    framework = "langchain"
    capabilities = {"observe": True, "native_callbacks": True, "pre_action": False, "pause_resume": False}

    def as_callback_handler(self):
        return LangChainCallbackHandler(self)

    async def on_chain_start(self, serialized, inputs):
        return await self.emit(
            "state.update", json_safe(dict(serialized=serialized, inputs=inputs, stage="chain_start"))
        )

    async def on_chain_end(self, outputs):
        return await self.emit("state.update", json_safe(dict(outputs=outputs, stage="chain_end")))

    async def on_llm_start(self, prompts, extra=None):
        return await self.emit("llm.request", json_safe(dict(prompts=prompts, **(extra or {}))))

    async def on_llm_end(self, generations, extra=None):
        return await self.emit("llm.response", json_safe(dict(generations=generations, **(extra or {}))))

    async def on_tool_start(self, name, input_str, extra=None):
        return await self.emit("tool.call.start", json_safe(dict(tool_name=name, input=input_str, **(extra or {}))))

    async def on_tool_end(self, name, output, extra=None):
        return await self.emit("tool.call.end", json_safe(dict(tool_name=name, output=output, **(extra or {}))))


class LangChainCallbackHandler(BaseCallbackHandler):
    run_inline = True
    raise_error = True

    def __init__(self, adapter):
        self.adapter = adapter
        self._tools = {}
        self._starts = {}

    def _dispatch(self, kind, payload, kwargs):
        native = str(kwargs.get("run_id", ""))
        parent = str(kwargs.get("parent_run_id") or "")
        # IDs are allocated before dispatch so concurrent callbacks can link safely.
        from uuid import uuid4

        event_id = uuid4().hex
        links = []
        if kind in {"tool.call.start", "llm.request"}:
            self._starts[native] = event_id
        elif native in self._starts:
            links = [self._starts.pop(native)]
        payload = json_safe(dict(payload, native_run_id=native))
        self.adapter.dispatch(
            self.adapter.emit(
                kind,
                payload,
                event_id=event_id,
                span_id=native.replace("-", "") or None,
                parent_span_id=parent.replace("-", "") or None,
                links=links,
            )
        )

    def on_chain_start(self, serialized, inputs, **kwargs):
        self._dispatch("state.update", dict(stage="chain_start", serialized=serialized, inputs=inputs), kwargs)

    def on_chain_end(self, outputs, **kwargs):
        self._dispatch("state.update", dict(stage="chain_end", outputs=outputs), kwargs)

    def on_chain_error(self, error, **kwargs):
        self._dispatch("agent.error", dict(error_type=type(error).__name__), kwargs)

    def on_llm_start(self, serialized, prompts, **kwargs):
        self._dispatch("llm.request", dict(prompts=prompts, serialized=serialized), kwargs)

    def on_chat_model_start(self, serialized, messages, **kwargs):
        self._dispatch("llm.request", dict(messages=messages, serialized=serialized), kwargs)

    def on_llm_new_token(self, token, **kwargs):
        self._dispatch("custom.llm.delta", dict(token=token), kwargs)

    def on_llm_end(self, response, **kwargs):
        self._dispatch("llm.response", dict(response=response), kwargs)

    def on_llm_error(self, error, **kwargs):
        self._dispatch("agent.error", dict(error_type=type(error).__name__), kwargs)

    def on_tool_start(self, serialized, input_str, **kwargs):
        name = str((serialized or {}).get("name", "unknown"))
        self._tools[str(kwargs.get("run_id", ""))] = name
        self._dispatch("tool.call.start", dict(tool_name=name, input=input_str), kwargs)

    def on_tool_end(self, output, **kwargs):
        name = self._tools.pop(str(kwargs.get("run_id", "")), kwargs.get("name", "unknown"))
        self._dispatch("tool.call.end", dict(tool_name=name, output=output), kwargs)

    def on_tool_error(self, error, **kwargs):
        name = self._tools.pop(str(kwargs.get("run_id", "")), "unknown")
        self._dispatch("tool.call.error", dict(tool_name=name, error_type=type(error).__name__), kwargs)
