from __future__ import annotations

import asyncio
import json
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from bson import json_util

from .config import Settings
from .contracts import WorkItemView
from .evaluation import OfficialEvaluationService
from .store import RunStore, utc_now


class TendMethodExecutor:
    """Thin adapter over the installed official TEND runtime and method implementations."""

    def __init__(self, settings: Settings, store: RunStore):
        self.settings = settings
        self.store = store
        self._runtimes: dict[str, Any] = {}
        self._index_caches: dict[str, Any] = {}
        self._runtime_locks: dict[str, asyncio.Lock] = {}
        self._artifact_locks: dict[str, asyncio.Lock] = {}
        self._compat_manifest_runs: set[str] = set()
        self._sample_cache: dict[tuple[str, str], dict[str, list[dict[str, Any]]]] = {}
        self._sample_locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._evaluation = OfficialEvaluationService(settings, store)

    async def __call__(self, item: WorkItemView) -> dict[str, Any]:
        run = self.store.get_run(item.run_id)
        if run is None:
            raise RuntimeError(f"run disappeared: {item.run_id}")
        if not self.settings.api_key_configured and not self.settings.llm_stub:
            raise RuntimeError("OPENAI_API_KEY is not configured")

        runtime = await self._runtime_for(item.run_id)
        started = time.perf_counter()
        if item.method_id == "sag_v3":
            prediction = await self._run_sag(runtime, item)
        else:
            prediction = await self._run_baseline(runtime, item)
        payload = prediction.to_json()
        if item.run_id in self._compat_manifest_runs:
            disclosure = payload.get("disclosure")
            if isinstance(disclosure, dict):
                disclosure["disjointness_ok"] = False
                detail = disclosure.setdefault("disjointness_detail", {})
                if isinstance(detail, dict):
                    detail["ok"] = False
                    detail["manifest_errors"] = [
                        "The public upstream release omits proposals/schemas/solver_allow_list.json; "
                        "model-disjointness cannot be independently verified."
                    ]
        payload.update(
            {
                "run_id": item.run_id,
                "work_item_id": item.id,
                "method_id": item.method_id,
                "nlq_track": item.track,
                "question": item.question,
                "model": run.model,
                "reasoning_effort": run.reasoning_effort,
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                "completed_at": utc_now(),
            }
        )
        if run.mode == "custom_query" and run.execute_custom_query and payload.get("MQL"):
            try:
                payload["execution_preview"] = await asyncio.to_thread(
                    self._execute_preview, runtime, item.db_id, str(payload["MQL"])
                )
            except Exception as error:  # noqa: BLE001 - generation result remains inspectable
                payload["execution_preview"] = {
                    "ok": False,
                    "error": f"{type(error).__name__}: {error}",
                    "rows": [],
                }
        await self._append_artifact(item.run_id, payload)
        return payload

    async def _runtime_for(self, run_id: str) -> Any:
        existing = self._runtimes.get(run_id)
        if existing is not None:
            return existing
        lock = self._runtime_locks.setdefault(run_id, asyncio.Lock())
        async with lock:
            existing = self._runtimes.get(run_id)
            if existing is not None:
                return existing
            run = self.store.get_run(run_id)
            if run is None:
                raise RuntimeError(f"run not found: {run_id}")
            from tend.cli import build_solver_runtime
            from tend.config import Settings as TendSettings

            overrides = {
                "OPENAI_API_KEY": self.settings.openai_api_key or "stub",
                "OPENAI_BASE_URL": self.settings.openai_base_url,
                "TEND_MODEL": run.model,
                "TEND_REASONING_EFFORT": run.reasoning_effort,
                "TEND_MONGO_URI": self.settings.mongodb_uri,
                "TEND_USE_EXISTING_MONGO_DBS": "1",
                "TEND_LLM_STUB": "1" if self.settings.llm_stub else "0",
                "TEND_LLM_MAX_CONCURRENCY": str(run.concurrency),
                "TEND_MAX_RETRIES": str(self.settings.provider_max_retries),
                "TEND_QUIET": "1",
            }
            tend_settings = TendSettings.from_env(
                run_id=run_id,
                overrides=overrides,
                require_bird=False,
                require_llm=True,
            )
            run_root = self.settings.runtime_dir / "official-tend-runs"
            upstream_schema_dir = tend_settings.paths.schemas
            if not (upstream_schema_dir / "solver_allow_list.json").is_file():
                upstream_schema_dir = Path(__file__).with_name("resources")
                self._compat_manifest_runs.add(run_id)
            paths = replace(
                tend_settings.paths,
                runs=run_root,
                dataset_out=run_root / run_id / "dataset",
                schemas=upstream_schema_dir,
            )
            tend_settings = replace(
                tend_settings,
                paths=paths,
                llm=replace(tend_settings.llm, max_tokens=None),
            )
            runtime = build_solver_runtime(tend_settings, run_kind="evaluation-system")
            if run.model.lower().startswith("gpt-5") and not self.settings.llm_stub:
                self._install_openai_chat_compat(runtime)
            self._runtimes[run_id] = runtime
            self._write_manifest(run_id, run, runtime)
            return runtime

    async def _run_baseline(self, runtime: Any, item: WorkItemView) -> Any:
        from tend.baselines.strategies import resolve_baselines
        from tend.baselines.workflow import run_baseline_record

        spec = resolve_baselines([item.method_id])[0]
        schema = self._load_schema(item.db_id)
        local_data = await self._samples_for(runtime, item.run_id, item.db_id)
        record = {
            "db_id": item.db_id,
            "record_id": item.record_id,
            "nl_queries": {"canonical": item.question},
            "nlq_track": item.track,
        }
        return await run_baseline_record(
            runtime.workflow,
            spec,
            record,
            schema,
            local_data=local_data,
            witness_k=self.settings.baseline_witness_k,
            batch_index=item.ordinal,
            input_mode="release" if item.record_id is not None else "nlq_db",
            nlq_track=item.track,
            evaluation_skip_reason=None if item.record_id is not None else "custom_query",
        )

    async def _run_sag(self, runtime: Any, item: WorkItemView) -> Any:
        from tend.solver.sag import GroundingIndexCache, SAGPolicy, sag_solve_nlq_db

        index_cache = self._index_caches.get(item.run_id)
        if index_cache is None:
            index_cache = GroundingIndexCache(runtime.mongo, runtime.settings, runtime.log)
            self._index_caches[item.run_id] = index_cache
        local_data = (
            await self._samples_for(runtime, item.run_id, item.db_id)
            if self.settings.llm_stub
            else None
        )
        return await sag_solve_nlq_db(
            runtime.workflow,
            db_id=item.db_id,
            nlq=item.question,
            record_id=item.record_id,
            policy=SAGPolicy(arm="v3"),
            index_cache=index_cache,
            local_data=local_data,
            stage="sag_v3",
        )

    async def _samples_for(
        self, runtime: Any, run_id: str, db_id: str
    ) -> dict[str, list[dict[str, Any]]]:
        key = (run_id, db_id)
        existing = self._sample_cache.get(key)
        if existing is not None:
            return existing
        lock = self._sample_locks.setdefault(key, asyncio.Lock())
        async with lock:
            existing = self._sample_cache.get(key)
            if existing is None:
                existing = await asyncio.to_thread(
                    runtime.mongo.snapshot_database,
                    db_id,
                    self.settings.baseline_sample_size,
                )
                self._sample_cache[key] = existing
            return existing

    def _load_schema(self, db_id: str) -> dict[str, Any]:
        path = self.settings.schema_dir / f"{db_id}.json"
        if not path.is_file():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _execute_preview(runtime: Any, db_id: str, mql: str, limit: int = 100) -> dict[str, Any]:
        from tend.execution.mongo import assert_no_disabled, parse_pipeline

        assert_no_disabled(mql)
        collection, pipeline = parse_pipeline(mql)
        bounded_pipeline = [*pipeline, {"$limit": limit}]
        raw = list(
            runtime.mongo.raw_database(db_id)[collection].aggregate(
                bounded_pipeline, maxTimeMS=20_000
            )
        )
        rows = json.loads(json_util.dumps(raw, default=str))
        return {
            "ok": True,
            "collection": collection,
            "rows": rows,
            "row_count": len(rows),
            "limit": limit,
            "possibly_truncated": len(rows) >= limit,
        }

    async def _append_artifact(self, run_id: str, payload: dict[str, Any]) -> None:
        lock = self._artifact_locks.setdefault(run_id, asyncio.Lock())
        async with lock:
            path = self.settings.runtime_dir / "runs" / run_id / "predictions.jsonl"
            await asyncio.to_thread(self._append_jsonl, path, payload)

    @staticmethod
    def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")

    def _write_manifest(self, run_id: str, run: Any, runtime: Any) -> None:
        manifest = {
            "run_id": run_id,
            "created_at": utc_now(),
            "mode": run.mode,
            "methods": run.method_ids,
            "tracks": run.tracks,
            "databases": run.database_ids,
            "model": run.model,
            "reasoning_effort": run.reasoning_effort,
            "concurrency": run.concurrency,
            "mongodb_uri_redacted": self._redact_mongo_uri(self.settings.mongodb_uri),
            "official_tend_commit": self._git_commit(self.settings.tend_source_dir),
            "official_tend_run_dir": str(runtime.settings.run_dir),
            "upstream_disjointness_manifest_available": run_id not in self._compat_manifest_runs,
            "provider_parameter_adapter": (
                "gpt5_chat_completions_no_temperature_no_output_cap"
                if run.model.lower().startswith("gpt-5")
                else "none"
            ),
        }
        path = self.settings.runtime_dir / "runs" / run_id / "manifest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    async def close_run(self, run_id: str) -> None:
        runtime = self._runtimes.pop(run_id, None)
        self._index_caches.pop(run_id, None)
        self._compat_manifest_runs.discard(run_id)
        for key in [key for key in self._sample_cache if key[0] == run_id]:
            self._sample_cache.pop(key, None)
            self._sample_locks.pop(key, None)
        if runtime is None:
            return
        runtime.mongo.close()
        await runtime.ctx.llm.aclose()
        runtime.log.close()

    async def finalize_run(self, run_id: str) -> None:
        state = self.store.get_evaluation(run_id)
        if state is None or state.status != "pending":
            return
        runtime = await self._runtime_for(run_id)
        await self._evaluation.finalize_run(run_id, runtime)

    async def aclose(self) -> None:
        for run_id in list(self._runtimes):
            await self.close_run(run_id)

    @staticmethod
    def _redact_mongo_uri(uri: str) -> str:
        if "@" not in uri:
            return uri
        prefix, suffix = uri.rsplit("@", 1)
        scheme = prefix.split("://", 1)[0]
        return f"{scheme}://***:***@{suffix}"

    @staticmethod
    def _git_commit(source_dir: Path) -> str | None:
        head = source_dir / ".git" / "HEAD"
        if not head.is_file():
            return None
        value = head.read_text(encoding="utf-8").strip()
        if value.startswith("ref: "):
            ref = source_dir / ".git" / value[5:]
            return ref.read_text(encoding="utf-8").strip() if ref.is_file() else None
        return value or None

    @staticmethod
    def _install_openai_chat_compat(runtime: Any) -> None:
        """Adapt the upstream OpenAI-compatible client to GPT-5 Chat Completions rules."""
        completions = runtime.ctx.llm._client.chat.completions
        original_create = completions.create

        async def create_compatible(*args: Any, **kwargs: Any) -> Any:
            model = str(kwargs.get("model") or "").lower()
            if model.startswith("gpt-5"):
                kwargs.pop("temperature", None)
                max_tokens = kwargs.pop("max_tokens", None)
                if max_tokens is not None:
                    kwargs["max_completion_tokens"] = max_tokens
            return await original_create(*args, **kwargs)

        completions.create = create_compatible
