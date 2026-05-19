import asyncio
import json
from datetime import datetime, timedelta, timezone

from fisheye.cli.main import main
from fisheye.collectors.journal import JournalStore
from fisheye.collectors.sqlite_store import SQLiteStore
from fisheye.migration import migrate
from fisheye.schema.events import EventEnvelope


def test_cli_evaluation_and_doctor_do_not_create_database(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["doctor", "--effective-config"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ok"
    assert main(["evaluate", "--split", "all"]) == 0
    assert json.loads(capsys.readouterr().out)["passed"] == 7
    assert not (tmp_path / "fisheye.db").exists()


def test_migration_keeps_source_untouched_and_has_dry_run(tmp_path):
    async def run():
        source = tmp_path / "old.db"
        old = SQLiteStore(source)
        old.raw_capture = True
        await old.handle_event(
            EventEnvelope(
                event_type="agent.start", agent_id="a", run_id="old-run", payload={"password": "legacy-secret"}
            )
        )
        old.close()
        before = source.read_bytes()
        assert (await migrate(source))["events"] == 1
        dest = tmp_path / "new.db"
        await migrate(source, dest, False)
        assert source.read_bytes() == before
        store = JournalStore(dest)
        assert len(await store.pending()) == 1
        assert (await store.pending())[0][1].workflow == "old-run"
        assert "legacy-secret" not in str(await store.get_run_events("old-run"))
        store.close()

    asyncio.run(run())


def test_v2_migration_preserves_scope_and_rejects_future_schema(tmp_path):
    import sqlite3

    import pytest

    async def run():
        source = tmp_path / "v2.db"
        store = JournalStore(source)
        event = EventEnvelope(
            event_type="agent.start", agent_id="a", run_id="r", application_id="team", workflow_id="w"
        )
        await store.accept(event)
        store.close()
        await migrate(source, tmp_path / "copy.db", False)
        copied = JournalStore(tmp_path / "copy.db")
        assert (await copied.pending())[0][1].scope == event.scope
        assert (await copied.accept(event)).duplicate
        copied.close()
        with sqlite3.connect(source) as conn:
            conn.execute("PRAGMA user_version=99")
        with pytest.raises(ValueError, match="newer"):
            await migrate(source)
        with pytest.raises(ValueError, match="newer"):
            JournalStore(source)

    asyncio.run(run())


def test_retention_keeps_pending_and_releases_inactive_state(tmp_path):
    async def run():
        store = JournalStore(tmp_path / "retention.db")
        event = EventEnvelope(event_type="agent.start", agent_id="a", run_id="r")
        receipt = await store.accept(event)
        await store.commit_analysis(receipt.sequence, event, {"version": 2})
        pending = EventEnvelope(event_type="agent.start", agent_id="b", run_id="pending")
        await store.accept(pending)
        result = await store.prune(now=datetime.now(timezone.utc) + timedelta(days=31))
        assert result == dict(events=1, checkpoints=1)
        assert len(await store.pending()) == 1
        store.close()

    asyncio.run(run())
