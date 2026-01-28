from __future__ import annotations

from typing import Any, Annotated

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse

from fisheye.api.dashboard import render_dashboard
from fisheye.collectors.http_ingest import HttpIngestService
from fisheye.schema.events import EventEnvelope


def register_routes(app: FastAPI, runtime: Any, api_key: str | None = None) -> None:
    ingest_service = HttpIngestService(runtime)

    async def _auth(x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None) -> None:
        if api_key is None:
            return
        if x_api_key != api_key:
            raise HTTPException(status_code=401, detail="Invalid API key")

    @app.get("/v1/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "runtime_started": getattr(runtime, "_started", False)}

    @app.post("/v1/events")
    async def ingest_events(payload: dict[str, Any] | list[dict[str, Any]], _: None = Depends(_auth)) -> dict[str, Any]:
        events_raw = payload if isinstance(payload, list) else [payload]
        events = [EventEnvelope.model_validate(item) for item in events_raw]
        ingested = await ingest_service.ingest(events)
        await runtime.drain(timeout=2.0)
        return {"ingested": ingested}

    @app.get("/v1/alerts")
    async def list_alerts(
        limit: int = 100,
        triggered_only: bool = False,
        min_score: float | None = None,
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
    async def list_runs(limit: int = 100, _: None = Depends(_auth)) -> list[dict[str, Any]]:
        return await runtime.store.list_runs(limit=limit)

    @app.get("/v1/runs/{run_id}/events")
    async def run_events(run_id: str, limit: int = 500, _: None = Depends(_auth)) -> list[dict[str, Any]]:
        return await runtime.store.get_run_events(run_id=run_id, limit=limit)

    @app.get("/v1/runs/{run_id}/alerts")
    async def run_alerts(run_id: str, limit: int = 200, _: None = Depends(_auth)) -> list[dict[str, Any]]:
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
    async def dashboard() -> str:
        alerts = await runtime.store.list_alerts(limit=30)
        runs = await runtime.store.list_runs(limit=30)
        return render_dashboard(alerts, runs)
