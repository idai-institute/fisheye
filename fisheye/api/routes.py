from __future__ import annotations

from typing import Any, Annotated
import hmac
import base64
from dataclasses import asdict

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import ValidationError
from fisheye.bus.async_bus import OverloadedError
from fisheye.collectors.journal import EventConflictError

from fisheye.api.dashboard import render_dashboard
from fisheye.collectors.http_ingest import HttpIngestService
from fisheye.schema.events import EventEnvelope


def register_routes(app: FastAPI, runtime: Any, api_key: str | None = None) -> None:
    ingest_service = HttpIngestService(runtime)

    async def _auth(x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
                    authorization: Annotated[str | None, Header()] = None) -> None:
        if api_key is None:
            return
        if x_api_key is None and authorization and authorization.startswith('Basic '):
            try:
                x_api_key = base64.b64decode(authorization[6:], validate=True).decode().split(':', 1)[1]
            except (ValueError, IndexError, UnicodeError):
                pass
        if x_api_key is None or not hmac.compare_digest(x_api_key, api_key):
            raise HTTPException(status_code=401, detail="Invalid API key", headers={'WWW-Authenticate':'Basic realm="Fisheye"'})

    @app.get("/v1/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "runtime_started": getattr(runtime, "_started", False)}

    @app.post("/v1/events")
    async def ingest_events(payload: dict[str, Any] | list[dict[str, Any]], _: None = Depends(_auth)) -> dict[str, Any]:
        events_raw = payload if isinstance(payload, list) else [payload]
        if not events_raw or len(events_raw) > 1000:
            raise HTTPException(status_code=413, detail="Batch must contain 1 to 1000 events")
        try:
            events = [EventEnvelope.model_validate(item) for item in events_raw]
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=[
                {"loc": list(e["loc"]), "type": e["type"], "msg": e["msg"]}
                for e in exc.errors(include_input=False)
            ]) from exc
        receipts = []
        for event in events:
            # Bind remote identities to the configured application and producer.
            event.application_id = runtime.config.api.application_id
            event.producer_id = runtime.config.api.producer_id
            event.meta.pop('_analysis', None)
            try:
                receipts.append(asdict(await runtime.publish(event)))
            except (OverloadedError, EventConflictError) as exc:
                raise HTTPException(status_code=429 if isinstance(exc, OverloadedError) else 409,
                                    detail={'error':str(exc),'accepted':receipts},headers={'Retry-After':'1'}) from exc
        await runtime.drain(timeout=2.0)
        return {"ingested": len(receipts), "receipts": receipts}

    @app.get("/v1/alerts")
    async def list_alerts(
        limit: int = Query(100, ge=1, le=1000),
        triggered_only: bool = False,
        min_score: float | None = Query(None, ge=0, le=1),
        _: None = Depends(_auth),
    ) -> list[dict[str, Any]]:
        return await runtime.store.list_alerts(limit=limit, triggered_only=triggered_only, min_score=min_score)

    @app.get("/v1/alerts/{alert_id}")
    async def get_alert(alert_id: str, _: None = Depends(_auth)) -> dict[str, Any]:
        alert = await runtime.store.get_alert(alert_id)
        if alert is None:
            raise HTTPException(status_code=404, detail="Alert not found")
        return alert

    @app.get("/v1/runs")
    async def list_runs(limit: int = Query(100, ge=1, le=1000), _: None = Depends(_auth)) -> list[dict[str, Any]]:
        return await runtime.store.list_runs(limit=limit)

    @app.get("/v1/runs/{run_id}/events")
    async def run_events(run_id: str, limit: int = Query(500, ge=1, le=1000), _: None = Depends(_auth)) -> list[dict[str, Any]]:
        return await runtime.store.get_run_events(run_id=run_id, limit=limit)

    @app.get("/v1/runs/{run_id}/alerts")
    async def run_alerts(run_id: str, limit: int = Query(200, ge=1, le=1000), _: None = Depends(_auth)) -> list[dict[str, Any]]:
        return await runtime.store.get_run_alerts(run_id=run_id, limit=limit)

    @app.get("/v1/detectors")
    async def list_detectors(_: None = Depends(_auth)) -> dict[str, Any]:
        engine = getattr(runtime, "detector_engine", None)
        detectors = engine.list_detector_ids() if engine else []
        observed = await runtime.store.list_detectors()
        return {"configured": detectors, "observed": observed}

    @app.get("/v1/metrics")
    async def metrics(_: None = Depends(_auth)) -> dict[str, Any]:
        return runtime.metrics

    @app.get("/", response_class=HTMLResponse)
    @app.get("/dashboard", response_class=HTMLResponse)
    async def dashboard(_: None = Depends(_auth)) -> str:
        alerts = await runtime.store.list_alerts(limit=30)
        runs = await runtime.store.list_runs(limit=30)
        workflows = await runtime.store.list_workflows(runtime.config.api.application_id)
        supervisor = getattr(runtime, 'supervisor', None)
        reviews = await supervisor.list_reviews() if supervisor else []
        return render_dashboard(alerts, runs, workflows, reviews)

    from fisheye.api.oversight import register_oversight_routes
    register_oversight_routes(app, runtime, _auth)
