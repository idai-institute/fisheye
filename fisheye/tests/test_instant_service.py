import asyncio
import os

from fisheye.runtime import build_default_runtime
from fisheye.tests.test_instant_store import record
from fisheye.tests.test_privacy_and_auth import config
from fisheye_instant.models import MailSettings, Rule
from fisheye_instant.service import InstantService


def test_countermeasures_execute_once_and_scope_shutdown(tmp_path):
    async def run():
        runtime = build_default_runtime(config(tmp_path))
        sent, stopped = [], []
        service = InstantService(
            runtime,
            stop_handlers={"w": lambda **kw: stopped.append(kw)},
            mailer=lambda settings, action: sent.append(action["id"]),
        )
        service.store.save_settings(
            [
                Rule(id="warning", name="Warning"),
                Rule(id="email", name="Email", action="email", recipients=["operator@example.org"]),
                Rule(id="stop", name="Stop", action="shutdown", workflow_id="w", environment="local"),
            ],
            MailSettings(host="smtp.example.org", sender="alerts@example.org"),
            1,
        )
        await record(runtime.store, 0.9, "risk")
        while await service.run_once():
            pass
        assert len(sent) == len(stopped) == 1
        assert stopped[0]["workflow_id"] == "w" and stopped[0]["environment"] == "local"
        assert {a["status"] for a in service.store.snapshot()["actions"]} == {"completed"}
        assert not await service.run_once()
        service.save_password("smtp-secret")
        assert service.settings()["password_set"] and "smtp-secret" not in str(service.settings())
        assert os.stat(service.secret_path).st_mode & 0o777 == 0o600
        runtime.close()

    asyncio.run(run())


def test_missing_shutdown_hook_is_blocked_and_mail_failure_recorded(tmp_path):
    async def run():
        runtime = build_default_runtime(config(tmp_path))
        service = InstantService(runtime)
        service.store.save_settings(
            [
                Rule(id="stop", name="Stop", action="shutdown"),
                Rule(id="mail", name="Mail", action="email", recipients=["operator@example.org"]),
            ],
            MailSettings(),
            1,
        )
        await record(runtime.store, 0.9, "risk")
        while await service.run_once():
            pass
        assert {a["status"] for a in service.store.snapshot()["actions"]} == {"blocked", "failed"}
        assert not await service.run_once()
        runtime.close()

    asyncio.run(run())
