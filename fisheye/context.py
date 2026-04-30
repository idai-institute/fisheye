"""Structured workflow instrumentation without a framework dependency."""

from __future__ import annotations

from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from uuid import uuid4

from fisheye.adapters.generic import GenericAdapter


@dataclass(frozen=True)
class TraceContext:
    workflow_id: str
    agent_id: str | None = None
    task_id: str | None = None
    trace_id: str | None = None


current_context: ContextVar[TraceContext | None] = ContextVar("fisheye_context", default=None)


class Workflow:
    def __init__(self, runtime, workflow_id: str | None = None, application_id="default", environment="local"):
        self.runtime = runtime
        self.workflow_id = workflow_id or uuid4().hex
        self.application_id = application_id
        self.environment = environment
        self.trace_id = uuid4().hex
        self._token = None

    def agent(self, agent_id, role=None, task_id=None):
        return Agent(self, agent_id, role, task_id)

    async def __aenter__(self):
        self._token = current_context.set(TraceContext(self.workflow_id, trace_id=self.trace_id))
        await self.agent("workflow").emit("workflow.start", {})
        return self

    async def __aexit__(self, exc_type, exc, tb):
        try:
            await self.agent("workflow").emit("workflow.stop", {"error_type": exc_type.__name__ if exc_type else None})
        finally:
            current_context.reset(self._token)


class Agent(GenericAdapter):
    def __init__(self, workflow, agent_id, role=None, task_id=None):
        super().__init__(workflow.runtime, agent_id, uuid4().hex)
        self.workflow = workflow
        self.role, self.task_id = role, task_id
        self.instance_id = uuid4().hex
        self._sequence = 0

    async def emit(self, event_type, payload, **kwargs):
        self._sequence += 1
        defaults = dict(
            schema_version="2",
            workflow_id=self.workflow.workflow_id,
            application_id=self.workflow.application_id,
            environment=self.workflow.environment,
            agent_instance_id=self.instance_id,
            agent_role=self.role,
            task_id=self.task_id,
            producer_id=self.instance_id,
            producer_sequence=self._sequence,
            trace_id=self.workflow.trace_id,
        )
        defaults.update(kwargs)
        return await super().emit(event_type, payload, **defaults)

    async def message(self, recipient, content="", sources=(), artifacts=(), trust="unknown"):
        return await self.emit(
            "message.sent",
            dict(
                recipient_id=recipient.agent_id if isinstance(recipient, Agent) else recipient,
                content=content,
                source_event_ids=[getattr(s, "event_id", s) for s in sources],
                artifact_ids=list(artifacts),
                trust=trust,
            ),
        )

    async def delegate(self, task_id, recipient, allowed_tools=(), parent_task_id=None, sources=()):
        return await self.emit(
            "task.delegated",
            dict(
                task_id=task_id,
                recipient_id=recipient.agent_id if isinstance(recipient, Agent) else recipient,
                allowed_tools=list(allowed_tools),
                parent_task_id=parent_task_id,
                source_event_ids=[getattr(s, "event_id", s) for s in sources],
            ),
        )

    async def usage(self, tokens=0, cost=0.0, calls=0):
        return await self.emit("usage.recorded", dict(tokens=tokens, cost=cost, calls=calls))

    def tool(self, name=None):
        def decorate(func):
            return self.wrap_tool(name or func.__name__, func)

        return decorate

    @asynccontextmanager
    async def task(self, task_id):
        token = current_context.set(
            TraceContext(self.workflow.workflow_id, self.agent_id, task_id, self.workflow.trace_id)
        )
        child = Agent(self.workflow, self.agent_id, self.role, task_id)
        await child.emit("task.status", dict(task_id=task_id, status="started"))
        try:
            yield child
        except BaseException:
            await child.emit("task.status", dict(task_id=task_id, status="failed"))
            raise
        else:
            await child.emit("task.status", dict(task_id=task_id, status="completed"))
        finally:
            current_context.reset(token)
