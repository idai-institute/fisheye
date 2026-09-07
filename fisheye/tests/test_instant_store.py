import asyncio

import pytest

from fisheye.collectors.journal import JournalStore
from fisheye.detectors.base import DetectorSignal
from fisheye.schema.events import EventEnvelope
from fisheye_instant.models import MailSettings, Rule
from fisheye_instant.store import Conflict, InstantStore


async def record(journal, score, event_id, application="default"):
    event = EventEnvelope(
        event_id=event_id, event_type="agent.start", agent_id="worker", run_id="w", application_id=application
    )
    receipt = await journal.accept(event)
    await journal.commit_analysis(
        receipt.sequence,
        event,
        {"coverage": {"test": "evaluated"}},
        signals=[DetectorSignal(detector_id="test", category="test", score=score)],
    )


def test_threshold_crossing_rearm_duplicates_and_restart(tmp_path):
    async def run():
        now = [1000]
        journal = JournalStore(tmp_path / "instant.db")
        store = InstantStore(journal, clock=lambda: now[0])
        await record(journal, 0.8, "one")
        await record(journal, 0.9, "two")
        assert len(store.snapshot()["actions"]) == 1
        assert store.snapshot()["selected"]["score"] == 90
        await record(journal, 1, "foreign", "other")
        assert store.snapshot()["selected"]["score"] == 90
        now[0] += 301
        assert store.snapshot()["selected"]["status"] == "stale"
        await record(journal, 0.8, "new-crossing")
        assert len(store.snapshot()["actions"]) == 2
        store.claim()
        journal.close()
        journal = JournalStore(tmp_path / "instant.db")
        store = InstantStore(journal, clock=lambda: now[0])
        store.recover()
        assert {a["status"] for a in store.snapshot()["actions"]} == {"pending", "unknown"}
        journal.close()

    asyncio.run(run())


def test_projection_rollback_and_rule_edits_cancel_queued_effects(tmp_path):
    async def run():
        journal = JournalStore(tmp_path / "rollback.db")
        store = InstantStore(journal)

        def fail(*args):
            raise ValueError("projection failed")

        journal.analysis_projections.append(fail)
        with pytest.raises(ValueError):
            await record(journal, 0.9, "rollback")
        assert not store.snapshot()["actions"] and not store.snapshot()["workflows"]
        assert len(await journal.pending()) == 1
        journal.analysis_projections.remove(fail)
        sequence, event = (await journal.pending())[0]
        await journal.commit_analysis(
            sequence,
            event,
            {"coverage": {"test": "evaluated"}},
            signals=[DetectorSignal(detector_id="test", category="test", score=0.9)],
        )
        config = store.settings()
        store.save_settings(
            [Rule(id="early-warning", name="Disabled", enabled=False)], MailSettings(), config["revision"]
        )
        with pytest.raises(Conflict):
            store.save_settings([], MailSettings(), config["revision"])
        assert store.claim() is None
        assert store.snapshot()["actions"][0]["status"] == "cancelled"
        journal.close()

    asyncio.run(run())
