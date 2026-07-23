import asyncio

import pytest

from fisheye.config import FisheyeConfig
from fisheye.context import TraceContext, Workflow, current_context


class Recorder:
    config = FisheyeConfig(api={"application_id": "configured-team"})

    def __init__(self):
        self.events = []
        self.failure = None

    async def publish(self, event):
        if self.failure:
            raise self.failure
        self.events.append(event)


@pytest.mark.parametrize("task_context", [False, True])
@pytest.mark.parametrize("failure", [OSError("capture unavailable"), asyncio.CancelledError()])
def test_failed_context_entry_restores_parent(task_context, failure):
    async def run():
        parent = TraceContext("parent")
        token = current_context.set(parent)
        runtime = Recorder()
        workflow = Workflow(runtime, "child")
        runtime.failure = failure
        context = workflow.agent("worker").task("t") if task_context else workflow
        try:
            with pytest.raises(type(failure)):
                async with context:
                    pytest.fail("Failed entry must not run the body")
            assert current_context.get() is parent
        finally:
            current_context.reset(token)

    asyncio.run(run())


def test_workflow_uses_configured_application_and_rejects_reentry():
    async def run():
        runtime = Recorder()
        workflow = Workflow(runtime, "w")
        async with workflow:
            active = current_context.get()
            with pytest.raises(RuntimeError, match="already active"):
                async with workflow:
                    pass
            assert current_context.get() is active
        assert current_context.get() is None
        assert {e.application_id for e in runtime.events} == {"configured-team"}
        assert Workflow(runtime, "override", application_id="other").application_id == "other"
        async with workflow:
            pass

    asyncio.run(run())


def test_task_cancellation_records_cancelled_status_and_restores_workflow():
    async def run():
        runtime = Recorder()
        async with Workflow(runtime, "w") as workflow:
            active = current_context.get()
            with pytest.raises(asyncio.CancelledError):
                async with workflow.agent("a").task("task"):
                    raise asyncio.CancelledError
            assert current_context.get() is active
            assert runtime.events[-1].payload["status"] == "cancelled"

    asyncio.run(run())
