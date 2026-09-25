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


def test_frontend_assets_and_demo_countermeasures_survive_restart(tmp_path):
    import time

    cfg = config(tmp_path)
    sent = []
    with TestClient(create_app(config=cfg, demo=True, mailer=lambda *args: sent.append(True))) as client:
        assert "Your agents, in view" in client.get("/").text
        assert client.get("/instant/static/app.js").status_code == 200
        assert client.get("/instant/static/app.css").status_code == 200
        settings = client.get("/instant/api/settings").json()
        body = {key: settings[key] for key in ("revision", "rules", "mail")}
        body["mail"].update(host="smtp.example.org", sender="alerts@example.org")
        body["password"] = "not-returned"
        body["rules"].extend(
            [
                dict(
                    id="stop-demo",
                    name="Stop sample",
                    threshold=85,
                    action="shutdown",
                    environment="demo",
                    workflow_id="research-demo",
                ),
                dict(
                    id="mail-demo",
                    name="Email preview",
                    threshold=60,
                    action="email",
                    recipients=["operator@example.org"],
                ),
            ]
        )
        result = client.put("/instant/api/settings", json=body)
        assert result.status_code == 200 and "not-returned" not in result.text
        assert client.post("/instant/api/demo", json={}).status_code == 200
        for _ in range(100):
            snapshot = client.get("/instant/api/snapshot").json()
            if len(snapshot["actions"]) == 3 and all(
                a["status"] in {"completed", "preview"} for a in snapshot["actions"]
            ):
                break
            time.sleep(0.02)
        else:
            raise AssertionError(snapshot["actions"])
        assert not snapshot["demo"]["running"] and not sent
        assert client.post("/instant/api/demo", json={}).status_code == 409
    with TestClient(create_app(config=cfg, demo=True)) as client:
        assert len(client.get("/instant/api/settings").json()["rules"]) == 3
        assert len(client.get("/instant/api/snapshot").json()["actions"]) == 3
