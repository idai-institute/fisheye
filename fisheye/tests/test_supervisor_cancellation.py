import asyncio
import threading

import pytest

from fisheye.collectors.journal import JournalStore
from fisheye.policies import Action, Policy, Supervisor


@pytest.mark.parametrize("allowed", [True, False])
def test_cancel_during_claim_waits_and_never_starts_tool(tmp_path, allowed):
    async def run():
        store = JournalStore(tmp_path / "claim.db")
        calls = []
        supervisor = Supervisor(
            store, Policy(allowed_tools={"send"} if allowed else set()), {"send": lambda: calls.append(1)}
        )
        action = Action(workflow_id="w", agent_id="a", tool_name="send")
        await supervisor.propose(action)
        entered, release = threading.Event(), threading.Event()
        expire = supervisor._expire

        def blocked():
            entered.set()
            assert release.wait(3)
            expire()

        supervisor._expire = blocked
        task = asyncio.create_task(supervisor.execute(action))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            for _ in range(2):
                task.cancel()
                await asyncio.sleep(0.01)
                assert not task.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert calls == []
        supervisor._expire = expire
        detail = await supervisor.get_review(action.action_id)
        assert detail["status"] == ("unknown" if allowed else "denied")
        if allowed:
            assert detail["result"]["phase"] == "claim"
        store.close()

    asyncio.run(run())


def test_cancel_during_completion_preserves_successful_terminal_state(tmp_path):
    async def run():
        store = JournalStore(tmp_path / "finish.db")
        supervisor = Supervisor(store, Policy(), {"send": lambda: 42})
        action = Action(workflow_id="w", agent_id="a", tool_name="send")
        await supervisor.propose(action)
        entered, release = threading.Event(), threading.Event()
        audit = supervisor._audit

        def blocked(actor, operation, data):
            audit(actor, operation, data)
            if operation == "action.completed":
                entered.set()
                assert release.wait(3)

        supervisor._audit = blocked
        task = asyncio.create_task(supervisor.execute(action))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            for _ in range(2):
                task.cancel()
                await asyncio.sleep(0.01)
                assert not task.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        detail = await supervisor.get_review(action.action_id)
        assert detail["status"] == "completed" and detail["result"] == {"result": 42}
        store.close()

    asyncio.run(run())


def test_completion_telemetry_failure_preserves_result_and_original_exception(tmp_path):
    class Unavailable:
        async def publish(self, event):
            raise OSError("sink unavailable")

    async def run():
        store = JournalStore(tmp_path / "telemetry.db")

        def fail():
            raise ValueError("tool failed")

        supervisor = Supervisor(store, Policy(), {"send": lambda: 42, "fail": fail})
        actions = [Action(workflow_id="w", agent_id="a", tool_name=name) for name in ("send", "fail")]
        for action in actions:
            await supervisor.propose(action)
        supervisor.runtime = Unavailable()
        assert await supervisor.execute(actions[0]) == 42
        with pytest.raises(ValueError, match="tool failed"):
            await supervisor.execute(actions[1])
        assert (await supervisor.get_review(actions[0].action_id))["status"] == "completed"
        assert (await supervisor.get_review(actions[1].action_id))["status"] == "failed"
        assert supervisor.metrics == {"completion_event_errors": 2, "last_completion_event_error": "OSError"}
        store.close()

    asyncio.run(run())
