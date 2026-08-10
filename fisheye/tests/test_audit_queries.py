import asyncio
import json
import sqlite3

from fastapi.testclient import TestClient

from fisheye.api.app import create_app
from fisheye.cli.main import main
from fisheye.collectors.journal import JournalStore
from fisheye.policies import Action, Policy, Supervisor
from fisheye.schema.domain import Finding
from fisheye.schema.events import EventEnvelope
from fisheye.tests.test_privacy_and_auth import config


async def seed(path):
    store = JournalStore(path)
    supervisor = Supervisor(store, Policy(), {"read": lambda: "ok"})
    first = Action(application_id="team", workflow_id="w", agent_id="a", tool_name="read")
    await supervisor.propose(first)
    await supervisor.propose(Action(application_id="other", workflow_id="w", agent_id="a", tool_name="read"))
    await supervisor.execute(first)
    await supervisor.record_usage(first.action_id, cost=1, tokens=2)
    event = EventEnvelope(application_id="team", workflow_id="w", event_type="agent.start", agent_id="a", run_id="r")
    receipt = await store.accept(event)
    finding = Finding(
        finding_id="issue", application_id="team", workflow_id="w", category="test", score=0.8, title="Issue"
    )
    await store.commit_analysis(receipt.sequence, event, {}, findings=[finding])
    await store.update_finding("issue", "resolved", "operator")
    store.close()


def test_audit_cursor_filters_before_pagination_and_keeps_scope_after_pruning(tmp_path):
    async def run():
        path = tmp_path / "audit.db"
        await seed(path)
        store = JournalStore(path)
        first = await store.audit_entries("team", limit=1)
        rest = await store.audit_entries("team", after=first[0]["id"])
        assert len(first + rest) == 5
        assert all(row["scope"] == ["team", "local", "w"] for row in first + rest)
        assert [row["operation"] for row in first + rest] == [
            "action.proposed",
            "action.executing",
            "action.completed",
            "action.usage",
            "finding.status",
        ]
        assert len(await store.audit_entries("team", operation="finding.status")) == 1
        assert not await store.audit_entries("team", scope=json.dumps(["other", "local", "w"], separators=(",", ":")))
        with store._conn:
            store._conn.execute("DELETE FROM journal")
            store._conn.execute("DELETE FROM findings")
        assert await store.audit_entries("team") == first + rest
        store.close()

    asyncio.run(run())


def test_legacy_audit_scope_upgrade_is_idempotent_and_hides_unattributed_rows(tmp_path):
    path = tmp_path / "legacy.db"
    asyncio.run(seed(path))
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            ALTER TABLE audit RENAME TO audit_new;
            CREATE TABLE audit (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,
                actor TEXT NOT NULL, operation TEXT NOT NULL, data_json TEXT NOT NULL);
            INSERT INTO audit SELECT id,timestamp,actor,operation,data_json FROM audit_new;
            DROP TABLE audit_new;
            INSERT INTO audit(timestamp,actor,operation,data_json) VALUES('2026-01-01','unknown','custom.legacy','{}');
        """)

    async def run():
        for _ in range(2):
            store = JournalStore(path)
            rows = await store.audit_entries("team")
            assert len(rows) == 5 and rows[-1]["operation"] == "finding.status"
            assert len(await store.audit_entries("other")) == 1
            assert len(store._query("SELECT * FROM audit")) == 7
            store.close()

    asyncio.run(run())


def test_api_and_cli_audit_use_configured_application(tmp_path, capsys):
    cfg = config(tmp_path)
    cfg.api.application_id = "team"
    cfg.api.api_key = "test-key"
    asyncio.run(seed(cfg.storage.sqlite_path))
    with TestClient(create_app(config=cfg)) as client:
        assert client.get("/v2/audit").status_code == 401
        headers = {"X-API-Key": "test-key"}
        response = client.get("/v2/audit?limit=1", headers=headers)
        page = response.json()
        assert response.status_code == 200 and len(page["items"]) == 1
        next_page = client.get(f"/v2/audit?after={page['next_cursor']}", headers=headers).json()
        assert len(next_page["items"]) == 4
        assert client.get("/v2/audit?after=-1", headers=headers).status_code == 422
    path = tmp_path / "config.json"
    path.write_text(cfg.model_dump_json())
    assert main(["--config", str(path), "audit", "--workflow-id", "w", "--operation", "finding.status"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert len(result["items"]) == 1 and result["items"][0]["data"]["status"] == "resolved"
