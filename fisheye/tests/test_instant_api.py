from fastapi.testclient import TestClient

from fisheye.tests.test_privacy_and_auth import config
from fisheye_instant.app import create_app


def test_setup_authentication_revision_conflicts_and_demo(tmp_path):
    cfg = config(tmp_path)
    cfg.api.api_key, cfg.api.review_api_key = "read-key", "write-key"
    with TestClient(create_app(config=cfg, demo=True)) as client:
        assert client.get("/instant/api/snapshot").status_code == 401
        reader = {"X-API-Key": "read-key"}
        writer = dict(reader, **{"X-Review-Key": "write-key"})
        settings = client.get("/instant/api/settings", headers=reader).json()
        body = {key: settings[key] for key in ("rules", "mail", "revision")}
        assert client.put("/instant/api/settings", headers=reader, json=body).status_code == 403
        assert (
            client.put(
                "/instant/api/settings", headers=dict(writer, Origin="https://other.example"), json=body
            ).status_code
            == 403
        )
        assert client.put("/instant/api/settings", headers=writer, json=body).status_code == 200
        assert client.put("/instant/api/settings", headers=writer, json=body).status_code == 409
        response = client.post("/instant/api/demo", headers=writer, json={})
        assert response.status_code == 200
        data = client.get("/instant/api/snapshot", headers=reader).json()
        assert data["selected"]["score"] > 60
        assert data["selected"]["environment"] == "demo"
        assert data["selected"]["channels"] and data["history"]


def test_shutdown_configuration_requires_host_hook_and_demo_is_opt_in(tmp_path):
    with TestClient(create_app(config=config(tmp_path))) as client:
        data = client.get("/instant/api/settings").json()
        body = {key: data[key] for key in ("rules", "mail", "revision")}
        body["rules"] = [dict(id="stop", name="Stop agent", action="shutdown", threshold=90)]
        assert client.put("/instant/api/settings", json=body).status_code == 422
        assert client.post("/instant/api/demo", json={}).status_code == 404
