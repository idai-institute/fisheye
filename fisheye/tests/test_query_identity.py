import asyncio
import base64

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from fisheye.api.app import create_app
from fisheye.cli.main import main
from fisheye.collectors.journal import JournalStore
from fisheye.config import FisheyeConfig
from fisheye.policies import Action, Policy, Supervisor
from fisheye.schema.events import EventEnvelope
from fisheye.tests.test_privacy_and_auth import config


def test_workflow_and_run_queries_preserve_agent_names_and_tied_pagination(tmp_path):
    async def run():
        store = JournalStore(tmp_path / "identity.db")
        for workflow in ("z", "a"):
            for agent in ("reader,planner", 'quoted " agent', "reader,planner"):
                await store.accept(EventEnvelope(event_type="agent.start", agent_id=agent, run_id=workflow))
        with store._conn:
            store._conn.execute("UPDATE journal SET accepted_at='2026-01-01T00:00:00+00:00'")
        first = await store.list_workflows("default", limit=1)
        second = await store.list_workflows("default", limit=1, offset=1)
        assert [first[0]["workflow_id"], second[0]["workflow_id"]] == ["a", "z"]
        assert first[0]["agent_ids"] == ['quoted " agent', "reader,planner"]
        runs = await store.scoped_runs("default")
        assert runs[0]["agent_ids"] == first[0]["agent_ids"]
        assert not await store.list_workflows("")
        store.close()

    asyncio.run(run())


def test_api_handles_unicode_credentials_and_rejects_empty_configuration(tmp_path):
    cfg = config(tmp_path)
    cfg.api.api_key = "clé-secrète"
    with TestClient(create_app(config=cfg)) as client:
        for password, status in [("clé-secrète", 200), ("mauvais-é", 401)]:
            encoded = base64.b64encode(("user:" + password).encode()).decode()
            assert client.get("/v2/health", headers={"Authorization": "Basic " + encoded}).status_code == status
    for field in ("application_id", "producer_id", "api_key", "review_api_key"):
        with pytest.raises(ValidationError):
            FisheyeConfig(api={field: ""})


def test_cli_review_mutation_cannot_cross_configured_application(tmp_path, capsys):
    cfg = config(tmp_path)
    cfg.api.application_id = "team"
    policy = Policy(review_tools={"send"})
    action = Action(application_id="other", workflow_id="w", agent_id="a", tool_name="send")

    async def prepare():
        store = JournalStore(cfg.storage.sqlite_path)
        await Supervisor(store, policy).propose(action)
        store.close()

    asyncio.run(prepare())
    config_path, policy_path = tmp_path / "config.json", tmp_path / "policy.json"
    config_path.write_text(cfg.model_dump_json())
    policy_path.write_text(policy.model_dump_json())
    with pytest.raises(SystemExit) as error:
        main(
            [
                "--config",
                str(config_path),
                "reviews",
                "approve",
                "--policy",
                str(policy_path),
                "--action-id",
                action.action_id,
                "--digest",
                action.digest,
            ]
        )
    assert error.value.code == 2 and "Review not found" in capsys.readouterr().err

    async def verify():
        store = JournalStore(cfg.storage.sqlite_path)
        assert (await Supervisor(store, policy).get_review(action.action_id))["status"] == "pending"
        store.close()

    asyncio.run(verify())
