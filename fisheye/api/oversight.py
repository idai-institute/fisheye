from __future__ import annotations

import hmac
import json
from typing import Annotated, Literal

from fastapi import Depends, Header, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict

from fisheye.api.dashboard import render_investigation
from fisheye.policies import ActionDenied


class ReviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_digest: str
    approve: bool


class FindingBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["open", "acknowledged", "resolved", "false_positive"]


def register_oversight_routes(app, runtime, auth):
    def scope(workflow_id, environment="local"):
        return json.dumps([runtime.config.api.application_id, environment, workflow_id], separators=(",", ":"))

    async def reviewer(x_review_key: Annotated[str | None, Header()] = None):
        expected = runtime.config.api.review_api_key
        if not expected or not x_review_key or not hmac.compare_digest(expected, x_review_key):
            raise HTTPException(403, "Reviewer credential required")
        return "operator"

    @app.get("/v2/workflows", dependencies=[Depends(auth)])
    async def workflows(limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
        return await runtime.store.list_workflows(runtime.config.api.application_id, limit, offset)

    @app.get("/v2/workflows/{workflow_id}/graph", dependencies=[Depends(auth)])
    async def graph(workflow_id: str, environment: str = "local"):
        return await runtime.store.workflow_graph(scope(workflow_id, environment))

    @app.get("/v2/workflows/{workflow_id}/events", dependencies=[Depends(auth)])
    async def events(
        workflow_id: str,
        environment: str = "local",
        after: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=1000),
    ):
        rows = await runtime.store.journal_events(scope(workflow_id, environment), after, limit)
        return dict(items=rows, next_cursor=rows[-1]["sequence"] if rows else None)

    @app.get("/v2/findings", dependencies=[Depends(auth)])
    async def findings(
        workflow_id: str,
        environment: str = "local",
        status: str | None = None,
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
    ):
        return await runtime.store.list_findings(scope(workflow_id, environment), status, limit, offset)

    @app.patch("/v2/findings/{finding_id}", dependencies=[Depends(auth)])
    async def update_finding(finding_id: str, body: FindingBody, actor: str = Depends(reviewer)):
        rows = await runtime.store.list_findings(limit=1000)
        if not any(
            f["finding_id"] == finding_id and f["application_id"] == runtime.config.api.application_id for f in rows
        ):
            raise HTTPException(404, "Finding not found")
        try:
            return await runtime.store.update_finding(finding_id, body.status, actor)
        except KeyError as exc:
            raise HTTPException(404, "Finding not found") from exc

    @app.get("/v2/reviews", dependencies=[Depends(auth)])
    async def reviews():
        supervisor = getattr(runtime, "supervisor", None)
        if supervisor is None:
            return []
        return [
            r
            for r in await supervisor.list_reviews()
            if r["action"]["application_id"] == runtime.config.api.application_id
        ]

    @app.post("/v2/reviews/{action_id}", dependencies=[Depends(auth)])
    async def decide(action_id: str, body: ReviewBody, actor: str = Depends(reviewer)):
        supervisor = getattr(runtime, "supervisor", None)
        if supervisor is None:
            raise HTTPException(409, "Supervision is not configured")
        visible = await supervisor.list_reviews(limit=1000)
        if not any(
            r["action_id"] == action_id and r["action"]["application_id"] == runtime.config.api.application_id
            for r in visible
        ):
            raise HTTPException(404, "Review not found")
        try:
            return (await supervisor.review_id(action_id, body.action_digest, body.approve, actor)).model_dump(
                mode="json"
            )
        except ActionDenied as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/v2/health", dependencies=[Depends(auth)])
    async def health():
        return dict(
            status="degraded" if runtime._analysis_error else "ok",
            journal=await runtime.store.journal_metrics(),
            coverage=runtime.analysis.coverage,
            delivery=runtime.metrics,
            config_version=runtime.config.fingerprint,
        )

    @app.get("/workflows/{workflow_id}", response_class=HTMLResponse, dependencies=[Depends(auth)])
    async def investigation(workflow_id: str, environment: str = "local"):
        key = scope(workflow_id, environment)
        graph = await runtime.store.workflow_graph(key)
        findings = await runtime.store.list_findings(key)
        return render_investigation(workflow_id, graph, findings)
