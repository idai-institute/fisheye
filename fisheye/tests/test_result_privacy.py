import asyncio
import math
from dataclasses import dataclass

from fisheye.collectors.journal import JournalStore
from fisheye.policies import Action, Policy, Supervisor
from fisheye.privacy import redact


def test_result_objects_are_normalized_before_redaction(tmp_path):
    secret = "sk-ABCDEFGHIJKLMNOPQRSTUV123456"

    class Opaque:
        def __str__(self):
            raise AssertionError("Private repr must not be inspected")

    @dataclass
    class Structured:
        api_key: str
        detail: str

    async def run():
        store = JournalStore(tmp_path / "results.db")
        results = [Opaque(), Structured(secret, "Contains " + secret)]
        supervisor = Supervisor(store, Policy(), {"read": lambda: results.pop(0)})
        for _ in range(2):
            action = Action(workflow_id="w", agent_id="a", tool_name="read")
            await supervisor.propose(action)
            await supervisor.execute(action)
            row = await supervisor.get_review(action.action_id)
            assert row["status"] == "completed"
            assert secret not in str(row["result"])
        assert row["result"]["result"]["api_key"] == "[REDACTED]"
        assert secret not in str(store._query("SELECT result_json FROM actions"))
        store.close()

    asyncio.run(run())
    assert redact(Structured(secret, secret)) == {"api_key": "[REDACTED]", "detail": "[REDACTED]"}


def test_unserializable_result_does_not_erase_completed_action(tmp_path):
    async def run():
        store = JournalStore(tmp_path / "invalid-result.db")
        calls = []

        def tool():
            calls.append(True)
            return float("nan")

        supervisor = Supervisor(store, Policy(), {"read": tool})
        action = Action(workflow_id="w", agent_id="a", tool_name="read")
        await supervisor.propose(action)
        assert math.isnan(await supervisor.execute(action))
        detail = await supervisor.get_review(action.action_id)
        assert detail["status"] == "completed"
        assert detail["result"] == {"capture_error": "ValueError"}
        assert calls == [True]
        store.close()

    asyncio.run(run())
