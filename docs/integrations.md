# Integration contracts

Framework bridges are optional and are tested against the versions below. Install one with `python -m pip install -e '.[langchain]'` (replace the extra as needed). The core library imports without any framework or server dependency.

| Integration | Tested package versions | Verified native behavior | Limits |
| --- | --- | --- | --- |
| Generic SDK | Core package | Workflow scopes, explicit messages, concurrent tool links, sync delivery, errors/cancellation | The host supplies causal sources and authoritative usage |
| LangChain | `langchain-core==0.3.63` | Real concurrent tools, tool exceptions, streamed chat tokens through completion | Observation callbacks; no execution gate or durable framework resume |
| OpenAI Agents | `openai-agents==0.0.17`, `openai==1.82.0` | Native `Runner` with a local fake model and real function tool, agent lifecycle | Hooks lack call IDs/arguments and streaming deltas; use explicit wrapper for exact per-call evidence |
| Camel | `camel-ai==0.2.58`, `openai==1.82.0` | Native `ChatAgent` with `StubModel`, `FunctionTool.async_call` | Explicit proxy; no internal model streaming or framework pause/resume |
| OpenTelemetry | API/SDK `1.33.1` | Real exported spans, trace/parent IDs and links, duration and token mapping | Explicit `record_span` bridge; no auto-installed global exporter or distributed graph joining |

The tests run locally without model API calls. Installing native integrations adds their upstream dependencies. Camel emits upstream deprecation warnings under Python 3.12; the tested example still passes. Compatibility beyond these exact framework versions is not asserted.

## LangChain

```python
from fisheye.adapters.langchain import LangChainAdapter

handler = LangChainAdapter(runtime, agent_id="researcher", run_id="workflow-1").as_callback_handler()
# Inside the runtime's async context:
result = await your_tool.ainvoke({"query": "example"}, config={"callbacks": [handler]})
await runtime.drain(timeout=10)
```

UUIDs and framework objects are normalized to JSON. Native run IDs link request/start events to streamed tokens and terminal events. Use separate adapter identities for distinct agents. Callback submission failures surface at drain.

## OpenAI Agents

```python
from agents import Runner, RunConfig
from fisheye.adapters.openai_agents import OpenAIAgentsAdapter

adapter = OpenAIAgentsAdapter(runtime, agent_id="worker", run_id="workflow-1")
result = await Runner.run(
    your_agent, "Your task", hooks=adapter.as_run_hooks(),
    run_config=RunConfig(tracing_disabled=True),
)
await runtime.drain(timeout=10)
```

Native agent names become event agent IDs. Hooks observe agent start/end, aggregate final usage, tool start/end, and handoff notifications. The pinned SDK invokes tool hooks in separate tasks alongside tool execution, so hooks cannot guarantee pre-action enforcement or reliably pair parallel calls by context alone. Such events explicitly report `correlation="unavailable_in_native_hook"`.

For exact call IDs, arguments, errors, cancellation, and latency, wrap the callable with `adapter.wrap_tool(name, callable)` before registering it with the framework. Avoid collecting both hook and wrapper tool events as if they were distinct billable calls. Use the host `Supervisor` for enforcement. Fisheye does not claim durable native SDK pause/resume or complete SDK streaming/error coverage.

## Camel

```python
from fisheye.adapters.camel import CamelAdapter

adapter = CamelAdapter(runtime, "worker", "workflow-1")
observed_tool = adapter.as_function_tool(your_async_function)
observed_agent = adapter.wrap_agent(your_chat_agent)
response = await observed_agent.astep("Your task")
```

The proxy supports `step` and `astep` and forwards other attributes. Use a synchronous Fisheye runtime context when invoking synchronous agent methods. Only calls through the proxy/wrapped tools are observed.

## OpenTelemetry and plugins

`OpenTelemetryAdapter(runtime).record_span(readable_span, workflow_id, agent_id)` converts a completed span to a versioned event. Mapping version `genai-1` records supported `gen_ai.*` attributes, parent/span links, and timing. It does not install or replace the application's tracer provider. Parent links become graph relationships only where referenced spans are recorded in the same workflow.

Custom detectors implement `Detector.analyze(event, context)` and may declare `PluginSpec`. Append them to `runtime.analysis.detectors` before starting the runtime. State must be serializable by the bounded checkpoint codec. Version changes reset incompatible plugin state. Core storage protocols are defined in `fisheye.collectors.protocols`; replacing the default SQLite engine requires a compatible runtime integration, not merely a collector callback.

`SemanticDetector` accepts a host-provided async `JudgeProvider`, explicit model/prompt versions, timeout, and cost reservation. No provider/network client is bundled or enabled by default. Treat evaluator inputs as untrusted data, validate structured results, and inspect advisory coverage/budget status. The provider owns prompting and actual usage reporting.
