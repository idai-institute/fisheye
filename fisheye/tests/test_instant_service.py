import asyncio
import os

import pytest

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


def test_smtp_requires_tls_and_never_includes_captured_evidence(tmp_path, monkeypatch):
    runtime = build_default_runtime(config(tmp_path))
    service = InstantService(runtime)
    service.save_password("private-password")
    calls = []

    class SMTP:
        def __init__(self, host, port, timeout):
            calls.append(("connect", host, port, timeout))

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def ehlo(self):
            calls.append("ehlo")

        def starttls(self, context):
            calls.append("tls")
            assert context.check_hostname

        def login(self, user, password):
            assert calls[-2:] == ["tls", "ehlo"]
            assert password == "private-password"
            calls.append("login")

        def send_message(self, message):
            assert "private-agent-output" not in message.as_string()
            assert message["To"] == "operator@example.org"
            calls.append("sent")

    monkeypatch.setattr("fisheye_instant.service.smtplib.SMTP", SMTP)
    service._send_mail(
        MailSettings(host="smtp.example.org", sender="alerts@example.org", username="user"),
        dict(
            id="id",
            scope='["default","local","w"]',
            score=80,
            rule=dict(name="Warn", recipients=["operator@example.org"], threshold=70),
            evidence="private-agent-output",
        ),
    )
    assert calls[-1] == "sent"
    runtime.close()


def test_interrupted_shutdown_is_not_retried(tmp_path):
    async def run():
        runtime = build_default_runtime(config(tmp_path))
        entered = asyncio.Event()

        async def stop(**kw):
            entered.set()
            await asyncio.Future()

        service = InstantService(runtime, stop_handlers={"w": stop})
        service.store.save_settings([Rule(id="stop", name="Stop", action="shutdown")], MailSettings(), 1)
        await record(runtime.store, 0.9, "risk")
        task = asyncio.create_task(service.run_once())
        await asyncio.wait_for(entered.wait(), 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert service.store.snapshot()["actions"][0]["status"] == "unknown"
        assert not await service.run_once()
        runtime.close()

    asyncio.run(run())
