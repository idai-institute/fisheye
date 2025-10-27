from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

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
            await runtime.stop()

    app = FastAPI(title="fisheye", version="0.1.0", lifespan=lifespan)
    register_routes(app, runtime, api_key=api_key)

    app.state.runtime = runtime
    return app
