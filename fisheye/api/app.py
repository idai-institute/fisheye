from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from fisheye.api.routes import register_routes
from fisheye.config import FisheyeConfig
from fisheye.runtime import FisheyeRuntime, build_default_runtime


def create_app(runtime: FisheyeRuntime | None = None, config: FisheyeConfig | None = None) -> FastAPI:
    runtime = runtime or build_default_runtime(config)
    api_key = runtime.config.api.api_key if hasattr(runtime, "config") else None

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await runtime.start()
        try:
            yield
        finally:
            await runtime.aclose()

    app = FastAPI(title="fisheye", version="0.3.0", lifespan=lifespan)

    @app.middleware("http")
    async def bound_request(request, call_next):
        limit = runtime.config.api.max_body_bytes
        if request.method in {"POST", "PUT", "PATCH"}:
            chunks = []
            size = 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit:
                    return JSONResponse({"detail": "Request body too large"}, status_code=413)
                chunks.append(chunk)
            request._body = b"".join(chunks)
        return await call_next(request)

    register_routes(app, runtime, api_key=api_key)

    app.state.runtime = runtime
    return app
