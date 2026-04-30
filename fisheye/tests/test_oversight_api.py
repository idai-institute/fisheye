import asyncio

from fastapi.testclient import TestClient

from fisheye.api.app import create_app
from fisheye.policies import Action, Policy
from fisheye.runtime import build_default_runtime
from fisheye.tests.test_privacy_and_auth import config


def test_workflow_graph_review_and_body_limit(tmp_path):
    cfg = config(tmp_path)
    cfg.api.api_key = "reader"
    cfg.api.review_api_key = "reviewer"
    cfg.api.max_body_bytes = 4096
    runtime = build_default_runtime(cfg)
    supervisor = runtime.supervise(Policy(review_tools={"send"}))
    a = Action(workflow_id="w", agent_id="a", tool_name="send")
    # Propose without starting the runtime in a temporary loop.
    supervisor.runtime = None
    asyncio.run(supervisor.propose(a))
    headers = {"X-API-Key": "reader"}
    with TestClient(create_app(runtime)) as client:
        event = dict(
            schema_version="2",
            application_id="forged",
            event_type="message.sent",
            agent_id="a",
            run_id="r",
            workflow_id="w",
            payload={"recipient_id": "b", "content": "hello"},
        )
        response = client.post("/v1/events", headers=headers, json=event)
        assert response.status_code == 200
        assert response.json()["receipts"][0]["durable"]
        assert client.get("/v2/workflows", headers=headers).json()[0]["application_id"] == "default"
        assert client.get("/workflows/w", headers=headers).status_code == 200
        review = client.get("/v2/reviews", headers=headers).json()[0]
        body = {"action_digest": review["action_digest"], "approve": True}
        assert client.post("/v2/reviews/" + a.action_id, headers=headers, json=body).status_code == 403
        assert (
            client.post(
                "/v2/reviews/" + a.action_id, headers=dict(headers, **{"X-Review-Key": "reviewer"}), json=body
            ).status_code
            == 200
        )
        assert client.post("/v1/events", headers=headers, content="x" * 5000).status_code == 413
