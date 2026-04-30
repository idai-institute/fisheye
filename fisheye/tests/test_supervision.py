import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from fisheye.collectors.journal import JournalStore
from fisheye.policies import Action, ActionDenied, Policy, ReviewRequired, Supervisor


def action(**kw):
    return Action(workflow_id="w", agent_id="a", tool_name="send", **kw)


def test_denied_pending_changed_and_expired_never_execute(tmp_path):
    async def run():
        calls = []
        store = JournalStore(tmp_path / "policy.db")
        now = datetime.now(timezone.utc)
        supervisor = Supervisor(
            store, Policy(review_tools={"send"}), {"send": lambda **kw: calls.append(kw)}, clock=lambda: now
        )
        a = action(arguments={"body": "hello"})
        assert (await supervisor.propose(a)).decision == "require_review"
        with pytest.raises(ReviewRequired):
            await supervisor.execute(a)
        with pytest.raises(ActionDenied):
            await supervisor.review(a, True, "intruder")
        changed = a.model_copy(update={"arguments": {"body": "changed"}})
        with pytest.raises(ActionDenied):
            await supervisor.review(changed, True, "operator")
        await supervisor.review(a, True, "operator")
        now += timedelta(hours=1)
        with pytest.raises(ActionDenied):
            await supervisor.execute(a)
        assert calls == []
        store.close()

    asyncio.run(run())


def test_reviews_survive_restart_and_approval_is_single_use(tmp_path):
    async def run():
        path = tmp_path / "restart.db"
        policy = Policy(review_tools={"send"})
        store = JournalStore(path)
        a = action()
        await Supervisor(store, policy).propose(a)
        store.close()
        store = JournalStore(path)
        calls = []
        supervisor = Supervisor(store, policy, {"send": lambda: calls.append(True)})
        assert len(await supervisor.list_reviews()) == 1
        await supervisor.review(a, True, "operator")
        await supervisor.execute(a)
        with pytest.raises(ActionDenied):
            await supervisor.execute(a)
        assert calls == [True]
        store.close()

    asyncio.run(run())


def test_concurrent_budget_reservations_cannot_overspend(tmp_path):
    async def run():
        store = JournalStore(tmp_path / "budget.db")
        supervisor = Supervisor(store, Policy(max_cost=10))
        decisions = await asyncio.gather(*(supervisor.propose(action(estimated_cost=6)) for _ in range(10)))
        assert sum(d.decision == "allow" for d in decisions) == 1
        assert sum(d.reason == "budget_exhausted" for d in decisions) == 9
        store.close()

    asyncio.run(run())


def test_destination_matching_is_exact_and_sensitive_egress_denied():
    policy = Policy(allowed_destinations={"trusted.example"})
    assert policy.evaluate(action(destination="https://trusted.example"))[0] == "allow"
    for url in ["https://trusted.example.evil.test", "https://trusted.example@evil.test", "file:///tmp/exfil"]:
        assert policy.evaluate(action(destination=url))[0] == "deny"
    assert policy.evaluate(action(destination="https://trusted.example", classification="secret"))[0] == "deny"
