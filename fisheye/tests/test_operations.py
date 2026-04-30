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
        await old.handle_event(EventEnvelope(event_type="agent.start", agent_id="a", run_id="old-run"))
        old.close()
        before = source.read_bytes()
        assert (await migrate(source))["events"] == 1
        dest = tmp_path / "new.db"
        await migrate(source, dest, False)
        assert source.read_bytes() == before
        store = JournalStore(dest)
        assert len(await store.pending()) == 1
        assert (await store.pending())[0][1].workflow == "old-run"
        store.close()

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
