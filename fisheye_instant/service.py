from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import smtplib
import ssl
import tempfile
from email.message import EmailMessage
from pathlib import Path

from fisheye.state.tasks import complete_on_cancel
from fisheye_instant.models import MailSettings
from fisheye_instant.store import InstantStore


class InstantService:
    """Own countermeasure execution on the same process as the analysis runtime.

    Stop callbacks receive workflow_id, environment, and an idempotency key.
    The trusted host decides how to stop its agents; no arbitrary shell is run.
    """

    def __init__(self, runtime, stop_handlers=None, mailer=None):
        self.runtime = runtime
        self.store = InstantStore(runtime.store, runtime.config.api.application_id)
        self.stop_handlers = {
            (key if isinstance(key, tuple) else ("local", key)): handler
            for key, handler in (stop_handlers or {}).items()
        }
        self.mailer = mailer or self._send_mail
        self._worker = None
        self.worker_error = None
        digest = hashlib.sha256(self.store.application_id.encode()).hexdigest()[:12]
        self.secret_path = Path(runtime.store.db_path).parent / f".instant-{digest}-secret"

    def save_password(self, password):
        if password is None:
            return
        fd, temporary = tempfile.mkstemp(prefix=".instant-secret-", dir=self.secret_path.parent)
        try:
            with os.fdopen(fd, "w") as stream:
                stream.write(password)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.secret_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def settings(self):
        return dict(
            self.store.settings(),
            password_set=self.secret_path.exists() and self.secret_path.stat().st_size > 0,
            shutdown_targets=[
                dict(environment=env, workflow_id=workflow) for env, workflow in sorted(self.stop_handlers)
            ],
        )

    async def start(self):
        # The runtime's analyzer lease must already be held before recovery.
        if not self.runtime._started:
            raise RuntimeError("Start the Fisheye runtime first")
        if self._worker is None:
            await asyncio.to_thread(self.store.recover)
            self._worker = asyncio.create_task(self._run())

    async def stop(self):
        if self._worker:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
            self._worker = None

    async def _run(self):
        while True:
            try:
                await self.run_once()
                self.worker_error = None
            except Exception as exc:
                self.worker_error = type(exc).__name__
            await asyncio.sleep(0.2)

    async def run_once(self):
        claim = asyncio.create_task(asyncio.to_thread(self.store.claim))
        try:
            action = await complete_on_cancel(claim)
        except asyncio.CancelledError:
            if not claim.cancelled() and claim.exception() is None and claim.result():
                await complete_on_cancel(
                    asyncio.to_thread(self.store.finish, claim.result()["id"], "unknown", "CancelledBeforeDispatch")
                )
            raise
        if action is None:
            return False
        try:
            if action["rule"]["action"] == "email":
                if json.loads(action["scope"])[1] == "demo":
                    await asyncio.to_thread(self.store.finish, action["id"], "preview")
                    return True
                settings = MailSettings.model_validate(self.store.settings()["mail"])
                if not settings.host or not settings.sender:
                    raise ValueError("Email delivery is not configured")
                await complete_on_cancel(asyncio.to_thread(self.mailer, settings, action))
            elif action["rule"]["action"] == "shutdown":
                app, environment, workflow = json.loads(action["scope"])
                handler = self.stop_handlers.get((environment, workflow))
                if handler is None:
                    await asyncio.to_thread(self.store.finish, action["id"], "blocked", "ShutdownHookUnavailable")
                    return True
                kwargs = dict(workflow_id=workflow, environment=environment, idempotency_key=action["id"])
                if inspect.iscoroutinefunction(handler):
                    await handler(**kwargs)
                else:
                    result = await complete_on_cancel(asyncio.to_thread(handler, **kwargs))
                    if inspect.isawaitable(result):
                        await result
            await complete_on_cancel(asyncio.to_thread(self.store.finish, action["id"], "completed"))
        except asyncio.CancelledError:
            await complete_on_cancel(asyncio.to_thread(self.store.finish, action["id"], "unknown", "CancelledError"))
            raise
        except Exception as exc:
            await complete_on_cancel(asyncio.to_thread(self.store.finish, action["id"], "failed", type(exc).__name__))
        return True

    def _send_mail(self, settings, action):
        workflow = json.loads(action["scope"])[2]
        message = EmailMessage()
        message["Subject"] = f"Fisheye warning: anomaly score {action['score']:.0f}"
        message["From"] = settings.sender
        message["To"] = ", ".join(action["rule"]["recipients"])
        message["Message-ID"] = f"<{action['id']}@fisheye.local>"
        message.set_content(
            f"Workflow: {workflow}\nAnomaly score: {action['score']}/100\n"
            f"Rule: {action['rule']['name']}\nThreshold: {action['rule']['threshold']}\n"
            f"Action reference: {action['id']}\n\nOpen Fisheye Instant to inspect the contributing signals."
        )
        context = ssl.create_default_context()
        if settings.security == "tls":
            smtp = smtplib.SMTP_SSL(settings.host, settings.port, timeout=10, context=context)
        else:
            smtp = smtplib.SMTP(settings.host, settings.port, timeout=10)
        with smtp:
            if settings.security == "starttls":
                smtp.ehlo()
                smtp.starttls(context=context)
                smtp.ehlo()
            if settings.username:
                password = self.secret_path.read_text() if self.secret_path.exists() else ""
                if not password:
                    raise ValueError("SMTP password is missing")
                smtp.login(settings.username, password)
            smtp.send_message(message)
