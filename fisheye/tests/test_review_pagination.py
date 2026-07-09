import asyncio

from fastapi.testclient import TestClient

from fisheye.api.app import create_app
from fisheye.policies import Action, Policy
from fisheye.runtime import build_default_runtime
from fisheye.tests.test_privacy_and_auth import config


def test_review_scope_is_applied_before_pagination_and_detail_access(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    cfg.api.api_key, cfg.api.review_api_key = "reader", "reviewer"
    runtime = build_default_runtime(cfg)
    supervisor = runtime.supervise(Policy(review_tools={"send"}))
    supervisor.runtime = None
    local = []

    async def populate():
        for application in ["foreign"] * 4 + ["default"] * 3:
            action = Action(application_id=application, workflow_id="w", agent_id="a", tool_name="send")
            await supervisor.propose(action)
            if application == "default":
                local.append(action)
        return action

    asyncio.run(populate())
    foreign = asyncio.run(supervisor.list_reviews(application_id="foreign"))[0]
    headers = {"X-API-Key": "reader"}
    with TestClient(create_app(runtime)) as client:
        first = client.get("/v2/reviews?limit=1", headers=headers).json()
        second = client.get("/v2/reviews?limit=1&offset=1", headers=headers).json()
        assert first[0]["action_id"] == local[0].action_id
        assert second[0]["action_id"] == local[1].action_id
        assert client.get("/v2/reviews?workflow_id=missing", headers=headers).json() == []
        assert client.get("/v2/reviews?limit=0", headers=headers).status_code == 422
        assert client.get("/v2/reviews/" + foreign["action_id"], headers=headers).status_code == 404

        async def empty_inbox(*args, **kwargs):
            return []

        # Decisions use direct scoped lookup, independent of a visible inbox page.
        monkeypatch.setattr(supervisor, "list_reviews", empty_inbox)
        current = local[2]
        response = client.post(
            "/v2/reviews/" + current.action_id,
            headers=dict(headers, **{"X-Review-Key": "reviewer"}),
            json={"action_digest": current.digest, "approve": True},
        )
        assert response.status_code == 200
        assert client.get("/v2/reviews/" + current.action_id, headers=headers).json()["status"] == "approved"
        response = client.post(
            "/v2/reviews/" + current.action_id,
            headers=dict(headers, **{"X-Review-Key": "reviewer"}),
            json={"action_digest": current.digest, "approve": True},
        )
        assert response.status_code == 409
