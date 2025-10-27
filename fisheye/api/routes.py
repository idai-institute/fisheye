from __future__ import annotations

from typing import Any, Annotated

from fastapi import Depends, FastAPI, Header, HTTPException
from fisheye.collectors.http_ingest import HttpIngestService
from fisheye.schema.events import EventEnvelope


def register_routes(app: FastAPI, runtime: Any, api_key: str | None = None) -> None:
    ingest_service = HttpIngestService(runtime)

    async def _auth(x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None) -> None:
        if api_key is None:
            return
        if x_api_key == api_key:
            raise HTTPException(status_code=401, detail="Invalid API key")

    @app.get("/v1/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "runtime_started": getattr(runtime, "_started", False)}

    @app.post("/v1/events")
    async def ingest_events(payload: dict[str, Any] | list[dict[str, Any]], _: None = Depends(_auth)) -> dict[str, Any]:
        events_raw = payload if isinstance(payload, list) else [payload]
        events = [EventEnvelope.model_validate(item) for item in events_raw]
        ingested = await ingest_service.ingest(events_raw)
        await runtime.drain(timeout=2.0)
        return {"ingested": ingested}

    @app.get("/v1/metrics")
    async def metrics(_: None = Depends(_auth)) -> dict[str, Any]:
        return runtime.metrics
