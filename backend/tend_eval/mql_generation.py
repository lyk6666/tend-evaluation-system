from __future__ import annotations

import json
import math
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections import Counter
from pathlib import Path
from typing import Any, Protocol

from bson import json_util
from pymongo import MongoClient

from .config import Settings
from .mql_contracts import (
    MQL_STAGE_NAMES,
    MQLCandidate,
    MQLDraftResponse,
    MQLExecutionPreview,
    MQLGenerationRunCreate,
    MQLGenerationRunView,
    MQLRepairAttempt,
    MQLStageTrace,
    MQLValidationIssue,
    TENDMetricResult,
    mql_now,
)
from .mql_store import MQLGenerationRunStore
from .retrieval_contracts import PrunedSchema, SchemaPruningRunView
from .retrieval_store import SchemaPruningRunStore
from .schema_indexing import normalize_text
from .store import AnchorRunStore


BANNED_OPERATORS = {
    "$out",
    "$merge",
    "$function",
    "$where",
    "$accumulator",
    "$sample",
    "$rand",
    "$currentOp",
    "$listSessions",
    "$listLocalSessions",
    "$changeStream",
    "$graphLookup",
    "$unionWith",
}
BANNED_SYSTEM_VARS = {"$$NOW", "$$CLUSTER_TIME"}
ORDER_SENSITIVE_OPS = {"$sort", "$limit", "$skip", "$setWindowFields"}


class MQLDraftGenerator(Protocol):
    def generate(
        self,
        *,
        question: str,
        schema_input: dict[str, Any],
        previous: MQLDraftResponse | None = None,
        issues: list[MQLValidationIssue] | None = None,
    ) -> MQLDraftResponse: ...


class OpenAIMQLGenerator:
    def __init__(self, settings: Settings):
        self.settings = settings

    def generate(
        self,
        *,
        question: str,
        schema_input: dict[str, Any],
        previous: MQLDraftResponse | None = None,
        issues: list[MQLValidationIssue] | None = None,
    ) -> MQLDraftResponse:
        if self.settings.llm_stub:
            collection = self._stub_collection(schema_input)
            return MQLDraftResponse(
                collection=collection,
                pipeline=[{"$limit": 5}],
                rationale="Deterministic stub pipeline for interface validation.",
            )
        if not self.settings.api_key_configured:
            raise RuntimeError("Configure OPENAI_API_KEY before generating MQL")
        repair_context = ""
        if previous is not None:
            repair_context = (
                "\nThe previous candidate was rejected. Repair it without using new schema paths.\n"
                f"Previous candidate: {previous.model_dump_json()}\n"
                f"Validation feedback: {json.dumps([item.model_dump(mode='json') for item in issues or []], ensure_ascii=False)}\n"
            )
        system = (
            "You generate one executable, read-only MongoDB aggregation pipeline. "
            "Use only the supplied pruned schema paths. A path containing [] denotes an array; "
            "a path containing * denotes a dynamic object key and normally requires $objectToArray. "
            "Use $lookup only when a supplied reference edge supports it. Never use $out, $merge, "
            "$function, $where, $accumulator, $sample, $rand, $$NOW, $graphLookup, or $unionWith. "
            "Return exactly one JSON object with keys collection, pipeline, rationale. "
            "pipeline must be a JSON array of MongoDB aggregation-stage objects."
        )
        user = (
            f"Question:\n{question}\n\n"
            f"Pruned schema alternative:\n{json.dumps(schema_input, ensure_ascii=False)}\n"
            f"{repair_context}"
        )
        payload = {
            "model": self.settings.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "reasoning_effort": self.settings.reasoning_effort,
            "response_format": {"type": "json_object"},
        }
        request = urllib.request.Request(
            f"{self.settings.openai_base_url.rstrip('/')}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.settings.openai_api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        attempt = 0
        while True:
            try:
                with urllib.request.urlopen(request, timeout=120) as response:
                    body = json.loads(response.read().decode("utf-8"))
                content = body["choices"][0]["message"]["content"]
                if isinstance(content, list):
                    content = "".join(
                        str(part.get("text", ""))
                        for part in content
                        if isinstance(part, dict)
                    )
                if not isinstance(content, str) or not content.strip():
                    raise ValueError("MQL provider returned empty content")
                return MQLDraftResponse.model_validate_json(strip_code_fence(content))
            except urllib.error.HTTPError as error:
                detail = error.read().decode("utf-8", errors="replace")[:500]
                failure: Exception = RuntimeError(
                    f"MQL provider returned HTTP {error.code}: {detail}"
                )
            except (urllib.error.URLError, TimeoutError, KeyError, ValueError, json.JSONDecodeError) as error:
                failure = error
            attempt += 1
            if self.settings.provider_max_retries >= 0 and attempt > self.settings.provider_max_retries:
                raise RuntimeError(
                    f"MQL generation failed after {attempt} attempts: {failure}"
                ) from failure
            time.sleep(
                min(
                    self.settings.retry_max_delay_seconds,
                    self.settings.retry_initial_delay_seconds * (2 ** max(0, attempt - 1)),
                )
            )

    @staticmethod
    def _stub_collection(schema_input: dict[str, Any]) -> str:
        nodes = schema_input.get("nodes") or []
        target = next((item for item in nodes if item.get("role") == "target"), None)
        selected = target or (nodes[0] if nodes else None)
        if not selected:
            raise RuntimeError("Stub generation received an empty pruned schema")
        return str(selected["collection"])


class MQLGenerationManager:
    def __init__(
        self,
        settings: Settings,
        store: MQLGenerationRunStore,
        pruning_store: SchemaPruningRunStore,
        anchor_store: AnchorRunStore,
        *,
        generator: MQLDraftGenerator | None = None,
    ):
        self.settings = settings
        self.store = store
        self.pruning_store = pruning_store
        self.anchor_store = anchor_store
        self.generator = generator or OpenAIMQLGenerator(settings)
        self._threads: dict[str, threading.Thread] = {}
        self._locks: dict[str, threading.Lock] = {}

    def create(self, request: MQLGenerationRunCreate) -> MQLGenerationRunView:
        pruning = self.pruning_store.get(request.pruning_run_id)
        if pruning is None or pruning.status != "completed" or not pruning.pruned_schemas:
            raise ValueError("Select a completed schema-pruning run with alternatives")
        anchor = self.anchor_store.get(pruning.bundle_run_id)
        if anchor is None or anchor.retrieval_bundle is None:
            raise ValueError("The pruning run's question bundle is unavailable")
        if not self.settings.provider_ready:
            raise RuntimeError("Configure OPENAI_API_KEY before generating MQL")
        available_ranks = {item.combination_rank for item in pruning.pruned_schemas}
        ranks = request.config.combination_ranks or sorted(available_ranks)
        if not ranks or not set(ranks) <= available_ranks:
            raise ValueError(
                f"combination_ranks must be selected from {sorted(available_ranks)}"
            )
        config = request.config.model_copy(update={"combination_ranks": sorted(set(ranks))})
        identifier = uuid.uuid4().hex
        run = MQLGenerationRunView(
            run_id=identifier,
            pruning_run_id=pruning.run_id,
            bundle_run_id=pruning.bundle_run_id,
            database_id=pruning.database_id,
            question=anchor.question,
            model_id=self.settings.model,
            config=config,
            stages=[MQLStageTrace(stage=name) for name in MQL_STAGE_NAMES],
        )
        self.store.save(run)
        return run

    def start(self, run_id: str) -> MQLGenerationRunView:
        run = self._require(run_id)
        if run.status in {"running", "completed"}:
            return run
        active = self._threads.get(run_id)
        if active and active.is_alive():
            return run
        run.status = "running"
        run.failure = None
        self.store.save(run)
        thread = threading.Thread(target=self._run, args=(run_id,), daemon=True)
        self._threads[run_id] = thread
        thread.start()
        return run

    def _run(self, run_id: str) -> None:
        lock = self._locks.setdefault(run_id, threading.Lock())
        if not lock.acquire(blocking=False):
            return
        client: MongoClient | None = None
        try:
            run = self._require(run_id)
            pruning = self.pruning_store.get(run.pruning_run_id)
            if pruning is None:
                raise RuntimeError("Schema-pruning run disappeared")
            schemas = [
                item
                for item in pruning.pruned_schemas
                if item.combination_rank in run.config.combination_ranks
            ]
            prepared = self._run_stage(
                run,
                "input_preparation",
                lambda: [compact_schema(item) for item in schemas],
                lambda value: (
                    f"Prepared {len(value)} question + pruned-schema inputs",
                    value,
                ),
            )

            candidates = self._run_stage(
                run,
                "candidate_generation",
                lambda: self._generate_candidates(run, schemas, prepared),
                lambda value: (
                    f"Generated {sum(item.status != 'generation_failed' for item in value)}/{len(value)} candidates",
                    [item.model_dump(mode="json") for item in value],
                ),
            )
            run.candidates = candidates
            self.store.save(run)

            client = MongoClient(
                self.settings.mongodb_uri,
                serverSelectionTimeoutMS=5_000,
                socketTimeoutMS=self.settings.evaluation_mongo_max_time_ms,
            )
            client.admin.command("ping")
            validator = MQLValidator(
                client,
                run.database_id,
                max_time_ms=self.settings.generation_mongo_max_time_ms,
            )
            schema_by_rank = {item.combination_rank: item for item in schemas}
            self._run_stage(
                run,
                "deterministic_validation",
                lambda: self._validate_candidates(run, candidates, schema_by_rank, validator),
                lambda value: (
                    f"Validated {sum(item.status == 'valid' for item in value)}/{len(value)} candidates",
                    [item.model_dump(mode="json") for item in value],
                ),
            )
            run.candidates = candidates
            self.store.save(run)

            self._run_stage(
                run,
                "repair",
                lambda: self._repair_candidates(run, candidates, schema_by_rank, validator),
                lambda value: (
                    f"{sum(len(item.repairs) for item in value)} repair attempt(s); {sum(item.status == 'valid' for item in value)} valid",
                    [item.model_dump(mode="json") for item in value],
                ),
            )
            run.candidates = candidates
            self.store.save(run)

            selected = self._run_stage(
                run,
                "execution_selection",
                lambda: self._execute_and_select(run, candidates, client),
                lambda value: (
                    f"Selected schema alternative {value.combination_rank}",
                    value.model_dump(mode="json"),
                ),
            )
            run.candidates = candidates
            run.selected_combination_rank = selected.combination_rank
            run.final_collection = selected.collection
            run.final_pipeline = selected.pipeline
            run.final_mql = selected.mql
            run.final_result = selected.execution
            self.store.save(run)

            metrics = self._run_stage(
                run,
                "tend_evaluation",
                lambda: evaluate_tend_metrics(run, selected, client, self.settings),
                lambda value: (
                    (
                        f"EXC={value.EXC}, EXF1={value.EXF1}, outcome={value.outcome}"
                        if value.available
                        else "No exact official TEND record match; metrics marked unavailable"
                    ),
                    value.model_dump(mode="json"),
                ),
            )
            run.tend_metrics = metrics
            run.status = "completed"
            self.store.save(run)
        except Exception as error:
            current = self.store.get(run_id)
            if current:
                current.status = "failed"
                current.failure = f"{type(error).__name__}: {error}"
                for stage in current.stages:
                    if stage.status == "running":
                        stage.status = "failed"
                        stage.error = current.failure
                        stage.completed_at = mql_now()
                self.store.save(current)
        finally:
            if client is not None:
                client.close()
            lock.release()

    def _generate_candidates(
        self,
        run: MQLGenerationRunView,
        schemas: list[PrunedSchema],
        prepared: list[dict[str, Any]],
    ) -> list[MQLCandidate]:
        result: list[MQLCandidate] = []
        stage = self._stage(run, "candidate_generation")
        for index, (schema, schema_input) in enumerate(zip(schemas, prepared, strict=True), start=1):
            try:
                draft = self.generator.generate(question=run.question, schema_input=schema_input)
                result.append(candidate_from_draft(schema, draft))
            except Exception as error:
                result.append(
                    MQLCandidate(
                        combination_rank=schema.combination_rank,
                        schema_score=schema.score,
                        status="generation_failed",
                        failure=f"{type(error).__name__}: {error}",
                    )
                )
            stage.progress = index / max(1, len(schemas))
            stage.summary = f"Generated alternative {index}/{len(schemas)}"
            self.store.save(run)
        return result

    def _validate_candidates(
        self,
        run: MQLGenerationRunView,
        candidates: list[MQLCandidate],
        schemas: dict[int, PrunedSchema],
        validator: "MQLValidator",
    ) -> list[MQLCandidate]:
        stage = self._stage(run, "deterministic_validation")
        for index, candidate in enumerate(candidates, start=1):
            if candidate.collection and candidate.pipeline:
                issues, used_paths = validator.validate(
                    candidate.collection,
                    candidate.pipeline,
                    schemas[candidate.combination_rank],
                    probe=True,
                )
                candidate.validation_issues = issues
                candidate.used_paths = used_paths
                candidate.status = "invalid" if has_errors(issues) else "valid"
                candidate.mql = render_mql(candidate.collection, candidate.pipeline)
            stage.progress = index / max(1, len(candidates))
            stage.summary = f"Validated alternative {index}/{len(candidates)}"
            self.store.save(run)
        return candidates

    def _repair_candidates(
        self,
        run: MQLGenerationRunView,
        candidates: list[MQLCandidate],
        schemas: dict[int, PrunedSchema],
        validator: "MQLValidator",
    ) -> list[MQLCandidate]:
        repairable = [item for item in candidates if item.status == "invalid"]
        schema_inputs = {rank: compact_schema(schema) for rank, schema in schemas.items()}
        stage = self._stage(run, "repair")
        total = max(1, len(repairable) * run.config.max_repair_attempts)
        completed = 0
        for candidate in repairable:
            for attempt_number in range(1, run.config.max_repair_attempts + 1):
                previous = MQLDraftResponse(
                    collection=candidate.collection or "unknown",
                    pipeline=candidate.pipeline,
                    rationale=candidate.rationale,
                )
                input_issues = list(candidate.validation_issues)
                try:
                    draft = self.generator.generate(
                        question=run.question,
                        schema_input=schema_inputs[candidate.combination_rank],
                        previous=previous,
                        issues=input_issues,
                    )
                    issues, used_paths = validator.validate(
                        draft.collection,
                        draft.pipeline,
                        schemas[candidate.combination_rank],
                        probe=True,
                    )
                    accepted = not has_errors(issues)
                    candidate.repairs.append(
                        MQLRepairAttempt(
                            attempt=attempt_number,
                            input_issues=input_issues,
                            collection=draft.collection,
                            pipeline=draft.pipeline,
                            rationale=draft.rationale,
                            validation_issues=issues,
                            accepted=accepted,
                        )
                    )
                    if accepted:
                        candidate.collection = draft.collection
                        candidate.pipeline = draft.pipeline
                        candidate.rationale = draft.rationale
                        candidate.validation_issues = issues
                        candidate.used_paths = used_paths
                        candidate.mql = render_mql(draft.collection, draft.pipeline)
                        candidate.status = "valid"
                        candidate.failure = None
                        break
                    candidate.validation_issues = issues
                except Exception as error:
                    issue = MQLValidationIssue(
                        severity="error",
                        code="REPAIR_GENERATION_FAILED",
                        message=f"{type(error).__name__}: {error}",
                    )
                    candidate.repairs.append(
                        MQLRepairAttempt(
                            attempt=attempt_number,
                            input_issues=input_issues,
                            validation_issues=[issue],
                            accepted=False,
                        )
                    )
                    candidate.validation_issues = [issue]
                completed += 1
                stage.progress = completed / total
                stage.summary = f"Completed {completed}/{total} allowed repairs"
                self.store.save(run)
        if not repairable or run.config.max_repair_attempts == 0:
            stage.progress = 1
        return candidates

    def _execute_and_select(
        self,
        run: MQLGenerationRunView,
        candidates: list[MQLCandidate],
        client: MongoClient,
    ) -> MQLCandidate:
        valid = sorted(
            (item for item in candidates if item.status == "valid"),
            key=lambda item: (-item.schema_score, item.combination_rank),
        )
        stage = self._stage(run, "execution_selection")
        for index, candidate in enumerate(valid, start=1):
            started = time.perf_counter()
            try:
                pipeline = [*candidate.pipeline, {"$limit": run.config.preview_limit}]
                raw = list(
                    client[run.database_id][candidate.collection].aggregate(
                        pipeline,
                        maxTimeMS=self.settings.generation_mongo_max_time_ms,
                    )
                )
                rows = json.loads(json_util.dumps(raw, default=str))
                candidate.execution = MQLExecutionPreview(
                    ok=True,
                    collection=candidate.collection or "",
                    rows=rows,
                    row_count=len(rows),
                    limit=run.config.preview_limit,
                    possibly_truncated=len(rows) >= run.config.preview_limit,
                    latency_ms=round((time.perf_counter() - started) * 1000, 3),
                )
                candidate.status = "executed"
            except Exception as error:
                candidate.status = "execution_failed"
                candidate.failure = f"{type(error).__name__}: {error}"
                candidate.execution = MQLExecutionPreview(
                    ok=False,
                    collection=candidate.collection or "",
                    limit=run.config.preview_limit,
                    latency_ms=round((time.perf_counter() - started) * 1000, 3),
                    error=candidate.failure,
                )
            stage.progress = index / max(1, len(valid))
            stage.summary = f"Executed candidate {index}/{len(valid)}"
            self.store.save(run)
        selected = next((item for item in valid if item.status == "executed"), None)
        if selected is None:
            raise RuntimeError("No generated MQL candidate passed validation and execution")
        selected.status = "selected"
        return selected

    def _run_stage(self, run, name, operation, summarize):
        stage = self._stage(run, name)
        stage.status = "running"
        stage.started_at = mql_now()
        stage.error = None
        self.store.save(run)
        started = time.perf_counter()
        try:
            value = operation()
            summary, artifact = summarize(value)
            stage.status = "completed"
            stage.progress = 1
            stage.summary = summary
            stage.artifact = artifact
            return value
        except Exception as error:
            stage.status = "failed"
            stage.error = f"{type(error).__name__}: {error}"
            raise
        finally:
            stage.completed_at = mql_now()
            stage.duration_ms = round((time.perf_counter() - started) * 1000, 3)
            self.store.save(run)

    @staticmethod
    def _stage(run: MQLGenerationRunView, name: str) -> MQLStageTrace:
        return next(item for item in run.stages if item.stage == name)

    def _require(self, run_id: str) -> MQLGenerationRunView:
        run = self.store.get(run_id)
        if run is None:
            raise KeyError(run_id)
        return run


class MQLValidator:
    def __init__(
        self,
        client: MongoClient,
        database_id: str,
        *,
        max_time_ms: int,
    ):
        self.client = client
        self.database_id = database_id
        self.max_time_ms = max_time_ms

    def validate(
        self,
        collection: str,
        pipeline: list[dict[str, Any]],
        schema: PrunedSchema,
        *,
        probe: bool,
    ) -> tuple[list[MQLValidationIssue], list[str]]:
        issues: list[MQLValidationIssue] = []
        used_paths: set[str] = set()
        collections = {node.collection for node in schema.nodes}
        if collection not in collections:
            issues.append(
                MQLValidationIssue(
                    severity="error",
                    code="COLLECTION_OUTSIDE_PRUNED_SCHEMA",
                    message=f"Collection {collection!r} is not in this pruned schema",
                )
            )
        if not pipeline:
            issues.append(
                MQLValidationIssue(
                    severity="error", code="EMPTY_PIPELINE", message="Pipeline is empty"
                )
            )
            return issues, []
        allowed = allowed_paths(schema)
        generated: set[str] = {"_id"}
        for index, stage in enumerate(pipeline):
            if not isinstance(stage, dict) or len(stage) != 1:
                issues.append(
                    MQLValidationIssue(
                        severity="error",
                        code="INVALID_STAGE_SHAPE",
                        message="Every pipeline stage must contain exactly one root operator",
                        stage_index=index,
                    )
                )
                continue
            operator, body = next(iter(stage.items()))
            if not isinstance(operator, str) or not operator.startswith("$"):
                issues.append(
                    MQLValidationIssue(
                        severity="error",
                        code="INVALID_ROOT_OPERATOR",
                        message=f"Invalid aggregation stage operator {operator!r}",
                        stage_index=index,
                    )
                )
                continue
            for token in walk_tokens(stage):
                if token in BANNED_OPERATORS or token in BANNED_SYSTEM_VARS:
                    issues.append(
                        MQLValidationIssue(
                            severity="error",
                            code="BANNED_OPERATOR",
                            message=f"Read-only deterministic policy forbids {token}",
                            stage_index=index,
                        )
                    )
            if operator == "$lookup":
                issues.extend(
                    validate_lookup(body, collection, schema, allowed, used_paths, index)
                )
            for reference in field_references(body):
                normalized = normalize_pipeline_path(reference)
                if is_allowed_reference(normalized, allowed.get(collection, set()), generated):
                    used_paths.add(normalized)
                else:
                    issues.append(
                        MQLValidationIssue(
                            severity="error",
                            code="PATH_OUTSIDE_PRUNED_SCHEMA",
                            message=f"Field reference {reference!r} is not grounded by the pruned schema",
                            stage_index=index,
                            path=reference,
                        )
                    )
            for key in stage_input_keys(operator, body):
                normalized = normalize_pipeline_path(key)
                if is_allowed_reference(normalized, allowed.get(collection, set()), generated):
                    used_paths.add(normalized)
                else:
                    issues.append(
                        MQLValidationIssue(
                            severity="error",
                            code="PATH_OUTSIDE_PRUNED_SCHEMA",
                            message=f"Stage key {key!r} is not grounded by the pruned schema",
                            stage_index=index,
                            path=key,
                        )
                    )
            generated.update(stage_generated_fields(operator, body))
        if not has_errors(issues) and probe:
            try:
                cursor = self.client[self.database_id][collection].aggregate(
                    [*pipeline, {"$limit": 1}], maxTimeMS=self.max_time_ms
                )
                try:
                    next(cursor, None)
                finally:
                    cursor.close()
                issues.append(
                    MQLValidationIssue(
                        severity="info",
                        code="MONGO_PROBE_OK",
                        message="MongoDB parsed and executed a one-row bounded probe",
                    )
                )
            except Exception as error:
                issues.append(
                    MQLValidationIssue(
                        severity="error",
                        code="MONGO_PROBE_FAILED",
                        message=f"{type(error).__name__}: {error}",
                    )
                )
        return deduplicate_issues(issues), sorted(used_paths)


def compact_schema(schema: PrunedSchema) -> dict[str, Any]:
    return {
        "combination_rank": schema.combination_rank,
        "schema_score": schema.score,
        "nodes": [
            {
                "id": node.id,
                "collection": node.collection,
                "path": node.path,
                "types": node.types,
                "role": node.role,
                "target_ids": node.target_ids,
                "binding": node.binding,
            }
            for node in schema.nodes
        ],
        "reference_edges": [edge.model_dump(mode="json") for edge in schema.reference_edges],
    }


def candidate_from_draft(schema: PrunedSchema, draft: MQLDraftResponse) -> MQLCandidate:
    return MQLCandidate(
        combination_rank=schema.combination_rank,
        schema_score=schema.score,
        status="generated",
        collection=draft.collection,
        pipeline=draft.pipeline,
        mql=render_mql(draft.collection, draft.pipeline),
        rationale=draft.rationale,
    )


def render_mql(collection: str, pipeline: list[dict[str, Any]]) -> str:
    return f"db.{collection}.aggregate({json.dumps(pipeline, ensure_ascii=False, separators=(',', ':'))})"


def strip_code_fence(value: str) -> str:
    stripped = value.strip()
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, flags=re.DOTALL | re.I)
    return match.group(1) if match else stripped


def has_errors(issues: list[MQLValidationIssue]) -> bool:
    return any(item.severity == "error" for item in issues)


def allowed_paths(schema: PrunedSchema) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for node in schema.nodes:
        if not node.path:
            continue
        path = normalize_schema_path(node.path)
        values = result.setdefault(node.collection, set())
        parts = path.split(".")
        values.update(".".join(parts[:index]) for index in range(1, len(parts) + 1))
    return result


def normalize_schema_path(value: str) -> str:
    return value.replace("[]", "").replace(".*", "").strip(".")


def normalize_pipeline_path(value: str) -> str:
    return value.lstrip("$").strip(".")


def is_allowed_reference(path: str, allowed: set[str], generated: set[str]) -> bool:
    if not path:
        return True
    root = path.split(".", 1)[0]
    if root in generated:
        return True
    return path in allowed or any(item.startswith(f"{path}.") for item in allowed)


def field_references(value: Any) -> set[str]:
    result: set[str] = set()
    if isinstance(value, str):
        if value.startswith("$") and not value.startswith("$$"):
            result.add(value[1:])
    elif isinstance(value, dict):
        for child in value.values():
            result.update(field_references(child))
    elif isinstance(value, list):
        for child in value:
            result.update(field_references(child))
    return result


def walk_tokens(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, child in value.items():
            yield str(key)
            yield from walk_tokens(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_tokens(child)


def stage_input_keys(operator: str, body: Any) -> set[str]:
    if not isinstance(body, dict):
        return set()
    if operator in {"$match", "$sort", "$unset"}:
        return {str(key) for key in body if not str(key).startswith("$")}
    if operator == "$project":
        return {
            str(key)
            for key, value in body.items()
            if (value == 1 or value is True) and not str(key).startswith("$")
        }
    return set()


def stage_generated_fields(operator: str, body: Any) -> set[str]:
    if not isinstance(body, dict):
        if operator == "$unwind" and isinstance(body, str):
            return {normalize_pipeline_path(body).split(".", 1)[0]}
        return set()
    if operator == "$group":
        return {"_id", *[str(key).split(".", 1)[0] for key in body if key != "_id"]}
    if operator in {"$project", "$addFields", "$set"}:
        return {
            str(key).split(".", 1)[0]
            for key, value in body.items()
            if not (value == 0 or value is False)
        }
    if operator == "$lookup" and isinstance(body.get("as"), str):
        return {body["as"].split(".", 1)[0]}
    return set()


def validate_lookup(
    body: Any,
    current_collection: str,
    schema: PrunedSchema,
    allowed: dict[str, set[str]],
    used_paths: set[str],
    stage_index: int,
) -> list[MQLValidationIssue]:
    if not isinstance(body, dict):
        return [
            MQLValidationIssue(
                severity="error",
                code="INVALID_LOOKUP",
                message="$lookup must be an object",
                stage_index=stage_index,
            )
        ]
    foreign = str(body.get("from") or "")
    pairs: list[tuple[str, str, str, str]] = []
    for edge in schema.reference_edges:
        source_collection, source_path = edge.source_id.split(":", 1)
        target_collection, target_path = edge.target_id.split(":", 1)
        pairs.append((source_collection, source_path, target_collection, target_path))
        pairs.append((target_collection, target_path, source_collection, source_path))
    compatible = [item for item in pairs if item[0] == current_collection and item[2] == foreign]
    issues: list[MQLValidationIssue] = []
    if not compatible:
        issues.append(
            MQLValidationIssue(
                severity="error",
                code="LOOKUP_WITHOUT_REFERENCE_EDGE",
                message=f"No supplied reference edge supports {current_collection} -> {foreign}",
                stage_index=stage_index,
            )
        )
        return issues
    local = body.get("localField")
    remote = body.get("foreignField")
    if isinstance(local, str) and isinstance(remote, str):
        normalized_local = normalize_pipeline_path(local)
        normalized_remote = normalize_pipeline_path(remote)
        used_paths.update({normalized_local, normalized_remote})
        exact = any(
            normalize_schema_path(source_path) == normalized_local
            and normalize_schema_path(target_path) == normalized_remote
            for _, source_path, _, target_path in compatible
        )
        if not exact:
            issues.append(
                MQLValidationIssue(
                    severity="error",
                    code="LOOKUP_FIELDS_DO_NOT_MATCH_REFERENCE",
                    message=f"{local} -> {foreign}.{remote} does not match a supplied reference edge",
                    stage_index=stage_index,
                )
            )
    elif "pipeline" not in body:
        issues.append(
            MQLValidationIssue(
                severity="error",
                code="INVALID_LOOKUP",
                message="$lookup requires localField/foreignField or a pipeline",
                stage_index=stage_index,
            )
        )
    if foreign not in allowed:
        issues.append(
            MQLValidationIssue(
                severity="error",
                code="LOOKUP_COLLECTION_OUTSIDE_PRUNED_SCHEMA",
                message=f"Lookup collection {foreign!r} is outside the pruned schema",
                stage_index=stage_index,
            )
        )
    return issues


def deduplicate_issues(issues: list[MQLValidationIssue]) -> list[MQLValidationIssue]:
    result: list[MQLValidationIssue] = []
    seen: set[tuple[Any, ...]] = set()
    for issue in issues:
        key = (issue.severity, issue.code, issue.message, issue.stage_index, issue.path)
        if key not in seen:
            seen.add(key)
            result.append(issue)
    return result


def evaluate_tend_metrics(
    run: MQLGenerationRunView,
    selected: MQLCandidate,
    client: MongoClient,
    settings: Settings,
) -> TENDMetricResult:
    if not settings.dataset_path.is_file():
        return TENDMetricResult(reason="The official TEND dataset is unavailable")
    records = json.loads(settings.dataset_path.read_text(encoding="utf-8"))
    normalized_question = normalize_text(run.question)
    matches: list[tuple[dict[str, Any], str]] = []
    for record in records:
        if str(record.get("db_id")) != run.database_id:
            continue
        if normalize_text(str(record.get("NLQ") or "")) == normalized_question:
            matches.append((record, "canonical"))
        elif normalize_text(str(record.get("NLQ_colloquial") or "")) == normalized_question:
            matches.append((record, "robustness"))
    if len(matches) != 1:
        return TENDMetricResult(
            reason=(
                "No exact official TEND task matches this database and question"
                if not matches
                else "More than one official TEND task matches this question"
            )
        )
    record, track = matches[0]
    try:
        from tend.evaluation.metrics import EXC_SURPLUS_BOUND, exf1
        from tend.execution.ast_check import parse_pipeline, root_ops
        from tend.execution.mongo import equiv_rec_values_superset, row_values_key

        gold_collection, gold_pipeline = parse_pipeline(str(record["MQL"]))
        predicted = list(
            client[run.database_id][selected.collection].aggregate(
                selected.pipeline,
                maxTimeMS=settings.evaluation_mongo_max_time_ms,
            )
        )
        gold = list(
            client[run.database_id][gold_collection].aggregate(
                gold_pipeline,
                maxTimeMS=settings.evaluation_mongo_max_time_ms,
            )
        )
        order_sensitive = bool(root_ops(gold_pipeline) & ORDER_SENSITIVE_OPS)
        exc = float(
            int(
                equiv_rec_values_superset(
                    gold,
                    predicted,
                    order_sensitive=order_sensitive,
                    max_surplus=EXC_SURPLUS_BOUND,
                )
            )
        )
        graded = float(exf1(predicted, gold))
        outcome = classify_outcome(
            exc=bool(exc),
            predicted=predicted,
            gold=gold,
            order_sensitive=order_sensitive,
            row_values_key=row_values_key,
        )
        return TENDMetricResult(
            available=True,
            reason="Exact official task match evaluated against its gold execution",
            record_id=record.get("record_id"),
            track=track,
            EXC=exc,
            EXF1=graded,
            outcome=outcome,
            claim_axes=derive_claim_axes(run.database_id, gold_pipeline),
            gold_row_count=len(gold),
            predicted_row_count=len(predicted),
        )
    except Exception as error:
        return TENDMetricResult(
            reason=f"Official TEND metric execution failed: {type(error).__name__}: {error}",
            record_id=record.get("record_id"),
            track=track,
        )


def classify_outcome(
    *,
    exc: bool,
    predicted: list[dict[str, Any]],
    gold: list[dict[str, Any]],
    order_sensitive: bool,
    row_values_key,
) -> str:
    if exc:
        return "correct"
    if not predicted and gold:
        return "empty"
    pred_counter = Counter(row_values_key(row) for row in predicted)
    gold_counter = Counter(row_values_key(row) for row in gold)
    if pred_counter == gold_counter:
        return "order_only" if order_sensitive else "value_mismatch"
    if not pred_counter - gold_counter:
        return "row_subset"
    if not gold_counter - pred_counter:
        return "row_superset"
    return "value_mismatch"


def derive_claim_axes(database_id: str, pipeline: list[dict[str, Any]]) -> dict[str, str]:
    root = {key for stage in pipeline if isinstance(stage, dict) for key in stage}
    joins = sum(
        1
        for stage in pipeline
        if isinstance(stage, dict)
        for key in stage
        if key in {"$lookup", "$graphLookup", "$unionWith"}
    )
    aggregation = root & {"$group", "$bucket", "$bucketAuto", "$facet", "$setWindowFields"}
    return {
        "domain": database_id,
        "join_depth": "3+" if joins >= 3 else str(joins),
        "aggregation_depth": (
            "shallow" if not aggregation else "medium" if len(pipeline) <= 4 else "deep"
        ),
        "schema_pattern": "unknown",
        "schema_flex": "none",
        "difficulty_tier": "unknown",
        "anti_sql_transfer_level": "unknown",
    }
