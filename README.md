# Fisheye

Fisheye observes cooperating agents, connects their actions to source evidence, and optionally gates tool execution through explicit policies. It runs locally as a Python library, with a SQLite journal, an investigation dashboard, and offline replay. Python 3.10+ is required.

Version 0.2 adds durable event acceptance, workflow graphs, correlated findings, shared budgets, persistent human reviews, and native framework bridges. Version 0.2.1 improves runtime recovery, replay consistency, graph traversal, review queries, and result capture. See [release notes](docs/release-notes.md) for verification results and remaining limits.

## Run a complete local example

From this checkout:

```bash
python -m pip install -e '.[server]'
python examples/multi_agent.py
fisheye --config examples/local.toml serve
```

Open http://127.0.0.1:8000. The example connects a researcher, planner, and executor, records an instruction propagated from an untrusted document, and denies a confidential upload before the tool runs. It uses no model credentials or external services. Data is written to `example-data/`.

## Instrument a workflow

Core installation needs only `python -m pip install -e .`:

```python
import asyncio
from fisheye import build_default_runtime

async def main():
    async with build_default_runtime() as runtime:
        async with runtime.workflow("report-001") as workflow:
            researcher = workflow.agent("researcher")
            writer = workflow.agent("writer")
            source = await researcher.emit(
                "tool.call.end",
                {"tool_name": "read_document", "output": "A public report", "trust": "untrusted"},
            )
            await researcher.message(writer, "Report received", sources=[source])

            @writer.tool()
            async def word_count(text: str) -> int:
                return len(text.split())

            assert await word_count("A public report") == 3
            await writer.usage(tokens=120, cost=0.001, calls=1)
        await runtime.drain(timeout=10)

asyncio.run(main())
```

Messages, delegations, and artifacts should carry explicit source IDs. Temporal proximity alone does not establish causality. Scope is `(application_id, environment, workflow_id)`; old events without a workflow retain their original run grouping.

Workflows inherit `api.application_id` from the runtime configuration unless explicitly overridden. Failed workflow/task entry restores the parent tracing context. Cancelled tasks emit a `cancelled` status.

For synchronous producers, one context owns a persistent background event loop:

```python
from fisheye import build_default_runtime

with build_default_runtime().sync() as runtime:
    receipt = runtime.publish({"event_type": "agent.start", "agent_id": "worker", "run_id": "run-001"})
    assert receipt.durable
    runtime.drain(timeout=10)
```

`publish()` returns after durable acceptance. `ingest(events)` atomically accepts a validated batch and returns receipts. Identical retries are idempotent; a conflicting event ID raises an error. `drain()` waits for analysis, exports, and current optional consumers, surfacing failures and timeouts. [Delivery and state contracts](docs/architecture.md) explain recovery and optional consumer limits.

## Observe or supervise

Observation records events and findings without changing agent decisions. Supervision requires the host to register tools with a `Supervisor`, propose an exact action, and execute it through that boundary. Policies cover tools, destinations, sensitive egress, delegation depth, active findings, and aggregate reservations. Human reviews survive restart and expire; approvals are bound to the action digest and policy version.

See [supervision](docs/supervision.md) for a runnable approval flow and usage reconciliation. Native framework callbacks are observation hooks. The host must use the execution boundary when it needs enforcement.

## Investigate and evaluate

```bash
fisheye doctor --effective-config
fisheye evaluate --split all
fisheye --config examples/local.toml inspect document-review
fisheye --config examples/local.toml export recording.jsonl
fisheye replay recording.jsonl --output before.json
fisheye --config changed.toml replay recording.jsonl --output after.json
fisheye compare before.json after.json
fisheye policy validate examples/review-policy.json
```

The dashboard shows workflows, a causal graph, event summaries, findings and evidence paths, health, and a review inbox. Complete captured events are available through the paginated API/export. Offline replay runs the default rules without calling tools or model services. The bundled seven-scenario corpus is a regression check, not a general detection accuracy benchmark.

## Configuration and privacy

Use `FisheyeConfig.load("config.toml", overrides={...})` or CLI `--config`. Precedence is defaults, file, `FISHEYE__SECTION__FIELD` environment values, then explicit overrides/CLI options. Unknown fields and invalid values fail validation. `doctor --effective-config` hides API credentials.

Capture defaults to `redacted`. `features` removes unstructured content while retaining approved structural fields and locally derived evidence. `raw` explicitly retains content. Redaction uses heuristic patterns; producers should avoid including unnecessary secrets. Application IDs and other identifiers should never contain credentials.

The local server binds to loopback. Configure `FISHEYE__API__API_KEY` for protected data access and a separate `FISHEYE__API__REVIEW_API_KEY` for review/finding changes. Remote CLI binding requires an API key. [Operations](docs/operations.md) covers deployment, API semantics, retention, backup, and migration.

## Integrations and development

Optional extras: `server`, `langchain`, `openai-agents`, `camel`, `telemetry`, and `dev`. See the [tested integration matrix](docs/integrations.md) for exact versions, examples, and capability limits.

```bash
python -m pip install -e '.[dev]'
python -m pytest -q
ruff check fisheye tools examples
ruff format --check fisheye tools examples
python -m build
python tools/wheel_smoke.py
python tools/benchmark.py --events 1000
```

The initial deployment boundary is one trusted application/team with one analysis runtime per database. Distributed analysis, enterprise tenancy, and production-scale performance certification remain outside this release. The [overhaul design](docs/overhaul-plan.md) records the broader roadmap; the release notes report what has been implemented and verified.
