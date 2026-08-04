from __future__ import annotations

from contextlib import asynccontextmanager
import asyncio
import json
from typing import Any

import uvicorn
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from . import __version__
from .anchors import AnchorExtractionPipeline
from .catalog import build_catalog
from .config import Settings, get_settings
from .contracts import (
    AnchorRunCreate,
    AnchorRunView,
    EvaluationView,
    RunCreate,
    RunView,
    WorkItemView,
    WorkStatus,
)
from .evaluation import load_result_records, load_results, resolve_export
from .executor import TendMethodExecutor
from .health import collect_health
from .orchestrator import RunOrchestrator, WorkExecutor
from .schema_contracts import SchemaIndexRunCreate, SchemaIndexRunView
from .schema_indexing import SchemaIndexManager
from .schema_store import SchemaIndexRunStore
from .retrieval_contracts import SchemaPruningRunCreate, SchemaPruningRunView
from .retrieval_store import SchemaPruningRunStore
from .schema_retrieval import SchemaPruningManager
from .store import AnchorRunStore, RunStore
from .workloads import build_work_items


def create_app(
    settings: Settings | None = None,
    *,
    executor: WorkExecutor | None = None,
) -> FastAPI:
    active_settings = settings or get_settings()
    store = RunStore(active_settings.sqlite_path)
    anchor_store = AnchorRunStore(active_settings.sqlite_path)
    anchor_pipeline = AnchorExtractionPipeline(active_settings, anchor_store)
    schema_index_store = SchemaIndexRunStore(active_settings.sqlite_path)
    schema_index_manager = SchemaIndexManager(active_settings, schema_index_store)
    schema_pruning_store = SchemaPruningRunStore(active_settings.sqlite_path)
    schema_pruning_manager = SchemaPruningManager(
        active_settings,
        schema_pruning_store,
        anchor_store,
        schema_index_store,
    )
    active_executor = executor or TendMethodExecutor(active_settings, store)
    orchestrator = RunOrchestrator(
        store,
        active_executor,
        retry_initial_delay=active_settings.retry_initial_delay_seconds,
        retry_max_delay=active_settings.retry_max_delay_seconds,
        max_generation_attempts=active_settings.max_generation_attempts,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        active_settings.runtime_dir.mkdir(parents=True, exist_ok=True)
        anchor_store.initialize()
        schema_index_store.initialize()
        schema_index_store.recover_interrupted()
        schema_pruning_store.initialize()
        schema_pruning_store.recover_interrupted()
        await orchestrator.start()
        try:
            yield
        finally:
            await orchestrator.stop()

    app = FastAPI(
        title="TEND Evaluation System",
        description="Automated benchmark evaluation and monitoring for TEND and SAG v3.",
        version=__version__,
        lifespan=lifespan,
    )
    app.state.settings = active_settings
    app.state.store = store
    app.state.orchestrator = orchestrator
    app.state.anchor_store = anchor_store
    app.state.anchor_pipeline = anchor_pipeline
    app.state.schema_index_store = schema_index_store
    app.state.schema_index_manager = schema_index_manager
    app.state.schema_pruning_store = schema_pruning_store
    app.state.schema_pruning_manager = schema_pruning_manager
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
            "embedding_model": current.embedding_model,
            "embedding_base_url": current.embedding_base_url,
            "embedding_ready": current.embedding_ready,
        }

    @app.post("/api/runs", response_model=RunView, status_code=status.HTTP_202_ACCEPTED)
    def create_run(payload: RunCreate, request: Request) -> RunView:
        settings_value: Settings = request.app.state.settings
        if not settings_value.provider_ready:
            raise HTTPException(
                status_code=409,
                detail="Configure OPENAI_API_KEY or enable TEND_EVAL_LLM_STUB before starting a run",
            )
        try:
            work_items = build_work_items(payload, settings_value)
        except (ValueError, FileNotFoundError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return request.app.state.store.create_run(
            payload,
            work_items,
            model=payload.model or settings_value.model,
            reasoning_effort=payload.reasoning_effort or settings_value.reasoning_effort,
            concurrency=payload.concurrency or settings_value.default_concurrency,
        )

    @app.get("/api/runs", response_model=list[RunView])
    def list_runs(request: Request, limit: int = Query(default=50, ge=1, le=200)):
        return request.app.state.store.list_runs(limit)

    @app.get("/api/runs/{run_id}", response_model=RunView)
    def get_run(run_id: str, request: Request) -> RunView:
        run = request.app.state.store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        return run

    @app.get("/api/runs/{run_id}/items", response_model=list[WorkItemView])
    def list_work_items(
        run_id: str,
        request: Request,
        limit: int = Query(default=100, ge=1, le=1_000),
        offset: int = Query(default=0, ge=0),
        item_status: WorkStatus | None = Query(default=None, alias="status"),
        recent: bool = Query(default=False),
    ):
        if request.app.state.store.get_run(run_id) is None:
            raise HTTPException(status_code=404, detail="run not found")
        return request.app.state.store.list_work_items(
            run_id, limit=limit, offset=offset, status=item_status, recent=recent
        )

    @app.post("/api/runs/{run_id}/pause", response_model=RunView)
    def pause_run(run_id: str, request: Request) -> RunView:
        run = request.app.state.store.pause(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        return run

    @app.post("/api/runs/{run_id}/resume", response_model=RunView)
    def resume_run(run_id: str, request: Request) -> RunView:
        run = request.app.state.store.resume(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        return run

    @app.post("/api/runs/{run_id}/cancel", response_model=RunView)
    def cancel_run(run_id: str, request: Request) -> RunView:
        run = request.app.state.store.request_cancel(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        return run

    @app.get("/api/runs/{run_id}/results")
    def run_results(run_id: str, request: Request) -> dict[str, Any]:
        if request.app.state.store.get_run(run_id) is None:
            raise HTTPException(status_code=404, detail="run not found")
        result = load_results(request.app.state.settings, request.app.state.store, run_id)
        if result is None:
            raise HTTPException(status_code=409, detail="custom-query runs do not have benchmark metrics")
        return result

    @app.get("/api/runs/{run_id}/results/records")
    def result_records(
        run_id: str,
        request: Request,
        track: str = Query(default="canonical"),
        system_id: str | None = Query(default=None),
        db_id: str | None = Query(default=None),
        outcome: str | None = Query(default=None),
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=1_000),
    ) -> dict[str, Any]:
        result = load_result_records(
            request.app.state.settings,
            request.app.state.store,
            run_id,
            track=track,
            system_id=system_id,
            db_id=db_id,
            outcome=outcome,
            offset=offset,
            limit=limit,
        )
        if result is None:
            raise HTTPException(status_code=404, detail="evaluation not found")
        return result

    @app.get("/api/runs/{run_id}/results/export/{track}/{kind}")
    def export_results(run_id: str, track: str, kind: str, request: Request) -> FileResponse:
        path = resolve_export(
            request.app.state.settings, request.app.state.store, run_id, track, kind
        )
        if path is None or not path.is_file():
            raise HTTPException(status_code=404, detail="result artifact not found")
        return FileResponse(path, filename=f"{run_id}-{track}-{path.name}")

    @app.post("/api/runs/{run_id}/evaluate", response_model=EvaluationView)
    def rerun_evaluation(run_id: str, request: Request) -> EvaluationView:
        evaluation = request.app.state.store.request_evaluation(run_id)
        if evaluation is None:
            raise HTTPException(
                status_code=409,
                detail="only terminal benchmark runs can be evaluated",
            )
        return evaluation

    @app.get("/api/runs/{run_id}/events")
    async def run_events(
        run_id: str,
        request: Request,
        after: int = Query(default=0, ge=0),
    ) -> StreamingResponse:
        if request.app.state.store.get_run(run_id) is None:
            raise HTTPException(status_code=404, detail="run not found")

        async def stream():
            cursor = after
            idle_ticks = 0
            while True:
                if await request.is_disconnected():
                    return
                events = request.app.state.store.list_events(run_id, after=cursor)
                if events:
                    idle_ticks = 0
                    for event in events:
                        cursor = event.id
                        data = event.model_dump(mode="json")
                        yield f"id: {event.id}\nevent: {event.type}\ndata: {json.dumps(data)}\n\n"
                else:
                    idle_ticks += 1
                    if idle_ticks >= 30:
                        yield ": keepalive\n\n"
                        idle_ticks = 0
                await asyncio.sleep(0.5)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post(
        "/api/new-methods/anchor-runs",
        response_model=AnchorRunView,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def create_anchor_run(
        payload: AnchorRunCreate,
        request: Request,
        background_tasks: BackgroundTasks,
    ) -> AnchorRunView:
        run = request.app.state.anchor_pipeline.create_run(payload)
        background_tasks.add_task(request.app.state.anchor_pipeline.run, run.run_id)
        return run

    @app.get("/api/new-methods/anchor-runs", response_model=list[AnchorRunView])
    def list_anchor_runs(
        request: Request,
        limit: int = Query(default=50, ge=1, le=200),
    ) -> list[AnchorRunView]:
        return request.app.state.anchor_store.list(limit)

    @app.get(
        "/api/new-methods/anchor-runs/{run_id}",
        response_model=AnchorRunView,
    )
    def get_anchor_run(run_id: str, request: Request) -> AnchorRunView:
        run = request.app.state.anchor_store.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="anchor extraction run not found")
        return run

    @app.delete(
        "/api/new-methods/anchor-runs/{run_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    def delete_anchor_run(run_id: str, request: Request) -> None:
        run = request.app.state.anchor_store.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="anchor extraction run not found")
        if run.status not in {"completed", "failed"}:
            raise HTTPException(status_code=409, detail="an active anchor run cannot be deleted")
        request.app.state.anchor_store.delete(run_id)

    @app.get("/api/new-methods/schema-indexes/databases", response_model=list[str])
    def schema_index_databases(request: Request) -> list[str]:
        return request.app.state.schema_index_manager.databases()

    @app.post(
        "/api/new-methods/schema-index-runs",
        response_model=SchemaIndexRunView,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def create_schema_index_run(
        payload: SchemaIndexRunCreate, request: Request
    ) -> SchemaIndexRunView:
        try:
            run = request.app.state.schema_index_manager.create(payload)
            return request.app.state.schema_index_manager.start(run.run_id)
        except (ValueError, RuntimeError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.get(
        "/api/new-methods/schema-index-runs",
        response_model=list[SchemaIndexRunView],
    )
    def list_schema_index_runs(
        request: Request,
        limit: int = Query(default=50, ge=1, le=500),
    ) -> list[SchemaIndexRunView]:
        return request.app.state.schema_index_store.list(limit)

    @app.get(
        "/api/new-methods/schema-index-runs/{run_id}",
        response_model=SchemaIndexRunView,
    )
    def get_schema_index_run(run_id: str, request: Request) -> SchemaIndexRunView:
        run = request.app.state.schema_index_store.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="schema index run not found")
        return run

    @app.post(
        "/api/new-methods/schema-index-runs/{run_id}/pause",
        response_model=SchemaIndexRunView,
    )
    def pause_schema_index_run(run_id: str, request: Request) -> SchemaIndexRunView:
        try:
            return request.app.state.schema_index_manager.pause(run_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.post(
        "/api/new-methods/schema-index-runs/{run_id}/resume",
        response_model=SchemaIndexRunView,
    )
    def resume_schema_index_run(run_id: str, request: Request) -> SchemaIndexRunView:
        try:
            return request.app.state.schema_index_manager.resume(run_id)
        except (KeyError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.post(
        "/api/new-methods/schema-index-runs/{run_id}/cancel",
        response_model=SchemaIndexRunView,
    )
    def cancel_schema_index_run(run_id: str, request: Request) -> SchemaIndexRunView:
        try:
            return request.app.state.schema_index_manager.cancel(run_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.post(
        "/api/new-methods/schema-pruning-runs",
        response_model=SchemaPruningRunView,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def create_schema_pruning_run(
        payload: SchemaPruningRunCreate, request: Request
    ) -> SchemaPruningRunView:
        try:
            run = request.app.state.schema_pruning_manager.create(payload)
            return request.app.state.schema_pruning_manager.start(run.run_id)
        except (ValueError, RuntimeError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.get(
        "/api/new-methods/schema-pruning-runs",
        response_model=list[SchemaPruningRunView],
    )
    def list_schema_pruning_runs(
        request: Request,
        limit: int = Query(default=50, ge=1, le=500),
    ) -> list[SchemaPruningRunView]:
        return request.app.state.schema_pruning_store.list(limit)

    @app.get(
        "/api/new-methods/schema-pruning-runs/{run_id}",
        response_model=SchemaPruningRunView,
    )
    def get_schema_pruning_run(run_id: str, request: Request) -> SchemaPruningRunView:
        run = request.app.state.schema_pruning_store.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="schema pruning run not found")
        return run

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
