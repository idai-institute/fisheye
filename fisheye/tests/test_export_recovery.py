import asyncio

from fisheye.runtime import build_default_runtime
from fisheye.tests.test_privacy_and_auth import config


def test_export_backlog_survives_sink_failure(tmp_path):
    async def run():
        cfg = config(tmp_path)
        runtime = build_default_runtime(cfg)
        original = runtime.logger.handle_batch

        async def broken(event):
            raise OSError("unavailable")

        runtime.logger.handle_batch = broken
        await runtime.publish(dict(event_type="agent.start", agent_id="a", run_id="r"))
        await asyncio.sleep(0.1)
        assert len(await runtime.store.pending_exports()) == 1
        assert (await runtime.store.journal_metrics())["pending"] == 0
        runtime.logger.handle_batch = original
        runtime._export_error = None
        await runtime.drain(5)
        await runtime.aclose()
        assert len(cfg.storage.events_jsonl_path.read_text().splitlines()) == 1

    asyncio.run(run())


def test_capture_profiles_preserve_structural_links(tmp_path):
    async def run(mode):
        target = tmp_path / mode
        target.mkdir()
        cfg = config(target)
        cfg.storage.capture = mode
        secret = "sk-ABCDEFGHIJKLMNOPQRSTUV123456"
        async with build_default_runtime(cfg) as runtime:
            await runtime.publish(
                dict(
                    schema_version="2",
                    event_type="message.sent",
                    agent_id="a",
                    run_id="r",
                    payload={"recipient_id": "b", "content": secret, "source_event_ids": ["source"]},
                    tags={"private": "unstructured value"},
                )
            )
            await runtime.drain(5)
            event = (await runtime.store.journal_events())[0]["event"]
            assert event["payload"]["source_event_ids"] == ["source"]
            assert (secret in str(event)) == (mode == "raw")
            if mode == "features":
                assert event["payload"]["content"] == "" and event["tags"] == {}

    for mode in ["raw", "redacted", "features"]:
        asyncio.run(run(mode))
