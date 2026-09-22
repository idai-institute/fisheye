"""Run a supervised sample agent and the Instant UI in one process.

python examples/instant_host.py
Create a shutdown rule targeting local / managed-agent at score 85.
"""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn

from fisheye import FisheyeConfig, build_default_runtime
from fisheye_instant.app import create_app

cfg = FisheyeConfig.from_dict(
    {
        "storage": {
            "sqlite_path": Path("instant-data/host.db"),
            "events_jsonl_path": Path("instant-data/host-events.jsonl"),
            "alerts_jsonl_path": Path("instant-data/host-alerts.jsonl"),
        }
    }
)
runtime = build_default_runtime(cfg)
stopping = asyncio.Event()


async def stop_agent(workflow_id, environment, idempotency_key):
    # A real host should stop its own task, process, or provider session here.
    # Scope is bound by the registration below. Repeated requests are harmless.
    stopping.set()


app = create_app(runtime, stop_handlers={("local", "managed-agent"): stop_agent})
instant_lifespan = app.router.lifespan_context


async def agent_loop():
    async with runtime.workflow("managed-agent") as workflow:
        agent = workflow.agent("researcher")
        while not stopping.is_set():
            await agent.emit("llm.message", {"content": "Reviewing public documents", "trust": "trusted"})
            try:
                await asyncio.wait_for(stopping.wait(), timeout=2)
            except asyncio.TimeoutError:
                pass


@asynccontextmanager
async def lifespan(app):
    async with instant_lifespan(app):
        task = asyncio.create_task(agent_loop())
        try:
            yield
        finally:
            stopping.set()
            await task


app.router.lifespan_context = lifespan

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
