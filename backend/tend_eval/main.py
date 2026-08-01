from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .catalog import build_catalog
from .config import Settings, get_settings
from .health import collect_health


def create_app(settings: Settings | None = None) -> FastAPI:
    active_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        active_settings.runtime_dir.mkdir(parents=True, exist_ok=True)
        yield

    app = FastAPI(
        title="TEND Evaluation System",
        description="Automated benchmark evaluation and monitoring for TEND and SAG v3.",
        version=__version__,
        lifespan=lifespan,
    )
    app.state.settings = active_settings
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            active_settings.frontend_origin,
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def health(request: Request) -> dict[str, Any]:
        return collect_health(request.app.state.settings)

    @app.get("/api/catalog")
    def catalog(request: Request) -> dict[str, Any]:
        return build_catalog(request.app.state.settings)

    @app.get("/api/config/defaults")
    def defaults(request: Request) -> dict[str, Any]:
        current: Settings = request.app.state.settings
        return {
            "provider": "openai-compatible",
            "model": current.model,
            "reasoning_effort": current.reasoning_effort,
            "concurrency": current.default_concurrency,
            "api_key_configured": current.api_key_configured,
        }

    @app.get("/")
    def root() -> dict[str, str]:
        return {"name": "TEND Evaluation System", "version": __version__}

    return app


app = create_app()


def run() -> None:
    settings = get_settings()
    uvicorn.run(
        "tend_eval.main:app",
        host=settings.backend_host,
        port=settings.backend_port,
        reload=False,
    )

