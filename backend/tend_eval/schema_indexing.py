from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import struct
import threading
import time
import unicodedata
import urllib.error
import urllib.request
import uuid
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Iterable

from bson import ObjectId
from pymongo import MongoClient

from .config import Settings
from .schema_contracts import (
    INDEX_STAGE_NAMES,
    IndexStageTrace,
    KeyProfile,
    SchemaIndexNode,
    SchemaIndexRunCreate,
    SchemaIndexRunView,
    SchemaReferenceEdge,
    ValueProfile,
    schema_now,
)
from .schema_store import SchemaIndexRunStore


TOKEN_RE = re.compile(r"[A-Za-z]+|\d+")
YEAR_RE = re.compile(r"^(?:18|19|20|21)\d{2}$")
INTEGER_KEY_RE = re.compile(r"^-?\d+$")


class BuildPaused(RuntimeError):
    pass


class BuildCancelled(RuntimeError):
    pass


def normalize_text(value: str) -> str:
    expanded = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", value)
    expanded = expanded.replace("_", " ").replace("-", " ").replace("[]", " ")
    expanded = expanded.replace("*", " ")
    return " ".join(match.group(0).lower() for match in TOKEN_RE.finditer(expanded))


def path_id(collection: str, path: str) -> str:
    return f"{collection}:{path or 'root'}"


def path_context(collection: str, path: str) -> str:
    segments = path.split(".")[:-1] if path else []
    return normalize_text(" ".join([collection, *segments]))


def last_segment(path: str) -> str:
    return path.rsplit(".", 1)[-1] if path else "root"


def canonical_value(value: Any) -> tuple[str, str, str] | None:
    if value is None:
        return None
    if isinstance(value, bool):
        text = "true" if value else "false"
        return "bool", text, text
    if isinstance(value, int):
        return "number", str(value), str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        normalized = format(value, ".15g")
        return "number", normalized, normalized
    if isinstance(value, (datetime, date)):
        normalized = value.isoformat()
        return "date", normalized, normalized
    if isinstance(value, ObjectId):
        normalized = str(value)
        return "objectid", normalized, normalized
    if isinstance(value, str):
        display = unicodedata.normalize("NFKC", value).strip()
        if not display or display.casefold() in {"n/a", "na", "null", "none", "unknown"}:
            return None
        return "string", display.casefold(), display
    return None


class EmbeddingClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self.settings.llm_stub:
            return [self._stub(text) for text in texts]
        key = self.settings.active_embedding_api_key
        if not key:
            raise RuntimeError("Configure TEND_EVAL_EMBEDDING_API_KEY or OPENAI_API_KEY")
        payload = json.dumps(
            {"model": self.settings.embedding_model, "input": texts}
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.settings.embedding_base_url.rstrip('/')}/embeddings",
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
        )
        attempt = 0
        while True:
            try:
                with urllib.request.urlopen(request, timeout=90) as response:
                    body = json.loads(response.read().decode("utf-8"))
                rows = sorted(body.get("data") or [], key=lambda item: item.get("index", 0))
                vectors = [item.get("embedding") for item in rows]
                self._validate(vectors, len(texts))
                return vectors
            except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as error:
                attempt += 1
                limit = self.settings.provider_max_retries
                if limit >= 0 and attempt > limit:
                    raise RuntimeError(f"Embedding API failed after {attempt} attempts: {error}") from error
                delay = min(
                    self.settings.retry_max_delay_seconds,
                    self.settings.retry_initial_delay_seconds * (2 ** max(0, attempt - 1)),
                )
                time.sleep(delay)

    @staticmethod
    def _stub(text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return [round((byte / 127.5) - 1, 6) for byte in digest[:16]]

    @staticmethod
    def _validate(vectors: list[Any], expected: int) -> None:
        if len(vectors) != expected or not vectors:
            raise ValueError(f"Embedding response contained {len(vectors)} vectors; expected {expected}")
        dimensions: set[int] = set()
        for vector in vectors:
            if not isinstance(vector, list) or not vector:
                raise ValueError("Embedding response contained an empty vector")
            if any(not isinstance(item, (int, float)) or not math.isfinite(item) for item in vector):
                raise ValueError("Embedding response contained a non-finite component")
            dimensions.add(len(vector))
        if len(dimensions) != 1:
            raise ValueError("Embedding response dimensions were inconsistent")


class SchemaTreeBuilder:
    def build(self, source: dict[str, Any]) -> list[SchemaIndexNode]:
        nodes: dict[str, SchemaIndexNode] = {}

        def add(
            collection: str,
            path: str,
            parent: str | None,
            types: Iterable[str],
            *,
            key_profile: KeyProfile | None = None,
        ) -> SchemaIndexNode:
            identifier = path_id(collection, path)
            search_text = (
                key_profile.semantic_type if key_profile else normalize_text(last_segment(path))
            )
            existing = nodes.get(identifier)
            if existing:
                existing.types = sorted(set(existing.types) | {str(item) for item in types if item})
                if key_profile:
                    existing.key_profile = key_profile
                    existing.search_text = key_profile.semantic_type
                return existing
            node = SchemaIndexNode(
                id=identifier,
                collection=collection,
                path=path,
                parent_id=parent,
                types=sorted({str(item) for item in types if item}),
                search_text=search_text or normalize_text(collection),
                context_text=path_context(collection, path),
                key_profile=key_profile,
            )
            nodes[identifier] = node
            return node

        def detect_dynamic(parent_path: str, properties: dict[str, Any]) -> KeyProfile | None:
            keys = list(properties)
            if len(keys) < 3:
                return None
            if all(YEAR_RE.fullmatch(key) for key in keys):
                values = sorted({int(key) for key in keys})
                return KeyProfile(
                    mode="enum",
                    semantic_type="year",
                    distinct_count=len(values),
                    min=min(values),
                    max=max(values),
                    values=values,
                )
            if len(keys) >= 5 and all(INTEGER_KEY_RE.fullmatch(key) for key in keys):
                values = sorted({int(key) for key in keys})
                parent_text = normalize_text(last_segment(parent_path))
                semantic = "position" if "position" in parent_text else "integer key"
                return KeyProfile(
                    mode="enum" if len(values) <= 256 else "range",
                    semantic_type=semantic,
                    distinct_count=len(values),
                    min=min(values),
                    max=max(values),
                    values=values if len(values) <= 256 else [],
                )
            return None

        def walk(
            collection: str,
            node: dict[str, Any],
            path: str,
            parent: str | None,
            key_profile: KeyProfile | None = None,
        ) -> None:
            raw_type = node.get("type")
            types = raw_type if isinstance(raw_type, list) else [raw_type] if raw_type else []
            current = add(collection, path, parent, types, key_profile=key_profile)
            properties = node.get("properties") if isinstance(node.get("properties"), dict) else {}
            dynamic = detect_dynamic(path, properties)
            if dynamic:
                wildcard_path = f"{path}.*" if path else "*"
                wildcard_id = path_id(collection, wildcard_path)
                for child in properties.values():
                    if isinstance(child, dict):
                        walk(collection, child, wildcard_path, current.id, dynamic)
                # Every merged child used the same stable wildcard ID.
                nodes[wildcard_id].key_profile = dynamic
            else:
                for name, child in properties.items():
                    if not isinstance(child, dict):
                        continue
                    child_path = f"{path}.{name}" if path else name
                    walk(collection, child, child_path, current.id)

            if "array" in types and isinstance(node.get("items"), dict):
                item = node["items"]
                item_properties = item.get("properties") if isinstance(item.get("properties"), dict) else {}
                for name, child in item_properties.items():
                    if isinstance(child, dict):
                        child_path = f"{path}[].{name}" if path else f"[].{name}"
                        walk(collection, child, child_path, current.id)

        collections = source.get("collections") or source.get("root", {}).get("collections") or {}
        for collection, descriptor in collections.items():
            root = add(collection, "", None, ["object"])
            schema = descriptor.get("schema") if isinstance(descriptor, dict) else None
            for name, child in (schema or {}).items():
                if isinstance(child, dict):
                    walk(collection, child, name, root.id)
        return sorted(nodes.values(), key=lambda item: item.id)


class SchemaIndexManager:
    def __init__(
        self,
        settings: Settings,
        store: SchemaIndexRunStore,
        *,
        embedding_client: EmbeddingClient | None = None,
    ):
        self.settings = settings
        self.store = store
        self.embedding_client = embedding_client or EmbeddingClient(settings)
        self.tree_builder = SchemaTreeBuilder()
        self._threads: dict[str, threading.Thread] = {}
        self._locks: dict[str, threading.Lock] = {}

    def databases(self) -> list[str]:
        return sorted(
            path.stem.removeprefix("schema_tree_")
            for path in self.settings.schema_tree_dir.glob("schema_tree_*.json")
        )

    def create(self, request: SchemaIndexRunCreate) -> SchemaIndexRunView:
        if request.database_id not in self.databases():
            raise ValueError(f"Unknown schema database: {request.database_id}")
        if not self.settings.embedding_ready:
            raise RuntimeError(
                "Configure TEND_EVAL_EMBEDDING_API_KEY or OPENAI_API_KEY before building an index"
            )
        identifier = uuid.uuid4().hex
        run = SchemaIndexRunView(
            run_id=identifier,
            index_id=identifier,
            database_id=request.database_id,
            embedding_model=self.settings.embedding_model,
            stages=[IndexStageTrace(stage=name) for name in INDEX_STAGE_NAMES],
        )
        self.store.save(run)
        return run

    def start(self, run_id: str) -> SchemaIndexRunView:
        run = self._require(run_id)
        if run.status == "completed":
            return run
        if run.status == "cancelled":
            raise ValueError("Cancelled index builds cannot be resumed")
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

    def pause(self, run_id: str) -> SchemaIndexRunView:
        run = self._require(run_id)
        if run.status == "running":
            run.status = "pausing"
            self.store.save(run)
        return run

    def resume(self, run_id: str) -> SchemaIndexRunView:
        run = self._require(run_id)
        if run.status not in {"paused", "failed", "created"}:
            return run
        return self.start(run_id)

    def cancel(self, run_id: str) -> SchemaIndexRunView:
        run = self._require(run_id)
        if run.status not in {"completed", "cancelled"}:
            run.status = "cancelled"
            for stage in run.stages:
                if stage.status in {"running", "paused"}:
                    stage.status = "cancelled"
            self.store.save(run)
        return run

    def _run(self, run_id: str) -> None:
        lock = self._locks.setdefault(run_id, threading.Lock())
        if not lock.acquire(blocking=False):
            return
        try:
            run = self._require(run_id)
            staging = self.settings.schema_index_dir / ".staging" / run_id
            staging.mkdir(parents=True, exist_ok=True)
            source_path = self.settings.schema_tree_dir / f"schema_tree_{run.database_id}.json"
            raw = source_path.read_bytes()
            run.schema_hash = hashlib.sha256(raw).hexdigest()
            self.store.save(run)

            nodes = self._schema_stage(run, staging, raw)
            nodes = self._value_stage(run, staging, nodes)
            references = self._reference_stage(run, staging, nodes)
            self._embedding_stage(run, staging, nodes)
            self._validation_stage(run, staging, nodes, references)

            final_dir = self.settings.schema_index_dir / run.database_id / run.index_id
            final_dir.parent.mkdir(parents=True, exist_ok=True)
            if final_dir.exists() and (final_dir / "READY").is_file():
                raise RuntimeError(f"Refusing to overwrite completed index directory: {final_dir}")
            shutil.copytree(staging, final_dir, dirs_exist_ok=True)
            (final_dir / "READY").write_text("validated\n", encoding="utf-8")
            run.artifact_dir = str(final_dir)
            run.status = "completed"
            run.failure = None
            self.store.save(run)
        except BuildPaused:
            pass
        except BuildCancelled:
            pass
        except Exception as error:
            run = self.store.get(run_id)
            if run and run.status != "cancelled":
                run.status = "failed"
                run.failure = f"{type(error).__name__}: {error}"
                for stage in run.stages:
                    if stage.status == "running":
                        stage.status = "failed"
                        stage.error = run.failure
                self.store.save(run)
        finally:
            lock.release()

    def _schema_stage(
        self, run: SchemaIndexRunView, staging: Path, raw: bytes
    ) -> list[SchemaIndexNode]:
        output = staging / "nodes.base.jsonl"
        stage = self._stage(run, "schema_traversal")
        if stage.status == "completed" and output.is_file():
            return self._read_nodes(output)
        self._start_stage(run, stage, "Traversing the complete schema tree")
        source = json.loads(raw)
        nodes = self.tree_builder.build(source)
        if not nodes:
            raise RuntimeError("Schema traversal produced zero index nodes")
        self._write_nodes(output, nodes)
        run.node_count = len(nodes)
        run.collection_count = len({item.collection for item in nodes})
        run.dynamic_key_count = sum(item.key_profile is not None for item in nodes)
        run.array_path_count = sum("[]" in item.path for item in nodes)
        self._complete_stage(
            run,
            stage,
            f"{run.node_count} nodes across {run.collection_count} collections",
            {
                "node_count": run.node_count,
                "collection_count": run.collection_count,
                "dynamic_key_count": run.dynamic_key_count,
                "array_path_count": run.array_path_count,
            },
        )
        self._check_control(run.run_id)
        return nodes

    def _value_stage(
        self, run: SchemaIndexRunView, staging: Path, nodes: list[SchemaIndexNode]
    ) -> list[SchemaIndexNode]:
        output = staging / "nodes.profiled.jsonl"
        stage = self._stage(run, "value_profiling")
        if stage.status == "completed" and output.is_file():
            return self._read_nodes(output)
        self._start_stage(run, stage, "Scanning complete MongoDB values")
        values_path = staging / "values.sqlite3"
        self._initialize_values(values_path)
        profiles_dir = staging / "value_profiles"
        profiles_dir.mkdir(exist_ok=True)
        by_collection: dict[str, list[SchemaIndexNode]] = defaultdict(list)
        for node in nodes:
            by_collection[node.collection].append(node)
        client = MongoClient(
            self.settings.mongodb_uri,
            serverSelectionTimeoutMS=5_000,
            socketTimeoutMS=self.settings.evaluation_mongo_max_time_ms,
        )
        client.admin.command("ping")
        database = client[run.database_id]
        collections = sorted(by_collection)
        for position, collection in enumerate(collections, start=1):
            profile_path = profiles_dir / f"{collection}.json"
            if not profile_path.is_file():
                self._profile_collection(
                    database[collection], by_collection[collection], values_path, profile_path
                )
            stage.progress = position / max(1, len(collections))
            stage.summary = f"Profiled {position}/{len(collections)} collections"
            self.store.save(run)
            self._check_control(run.run_id)

        profile_payload: dict[str, Any] = {}
        for profile_path in profiles_dir.glob("*.json"):
            profile_payload.update(json.loads(profile_path.read_text(encoding="utf-8")))
        for node in nodes:
            item = profile_payload.get(node.id) or {}
            if "value_profile" in item:
                node.value_profile = ValueProfile.model_validate(item["value_profile"])
            if "key_profile" in item:
                node.key_profile = KeyProfile.model_validate(item["key_profile"])
                node.search_text = node.key_profile.semantic_type
        self._write_nodes(output, nodes)
        with sqlite3.connect(values_path) as connection:
            run.value_count = int(
                connection.execute("SELECT COUNT(*) FROM path_values").fetchone()[0]
            ) + int(connection.execute("SELECT COUNT(*) FROM key_values").fetchone()[0])
        self._complete_stage(
            run,
            stage,
            f"{run.value_count} exact distinct values indexed",
            {"value_count": run.value_count, "exact_membership": True},
        )
        self._check_control(run.run_id)
        return nodes

    def _profile_collection(
        self,
        collection: Any,
        nodes: list[SchemaIndexNode],
        values_path: Path,
        profile_path: Path,
    ) -> None:
        node_by_path = {node.path: node for node in nodes}
        values: dict[str, dict[tuple[str, str], str]] = defaultdict(dict)
        keys: dict[str, dict[tuple[str, str], str]] = defaultdict(dict)

        def visit(value: Any, current_path: str) -> None:
            if isinstance(value, dict):
                for name, child in value.items():
                    exact = f"{current_path}.{name}" if current_path else name
                    wildcard = f"{current_path}.*" if current_path else "*"
                    if exact in node_by_path:
                        next_path = exact
                    elif wildcard in node_by_path and node_by_path[wildcard].key_profile:
                        next_path = wildcard
                        normalized_key = canonical_value(name)
                        if normalized_key:
                            kind, normalized, display = normalized_key
                            keys[node_by_path[wildcard].id][(kind, normalized)] = display
                    else:
                        continue
                    visit(child, next_path)
                return
            if isinstance(value, list):
                if not value:
                    return
                array_path = f"{current_path}[]"
                has_array_children = any(
                    path.startswith(f"{array_path}.") for path in node_by_path
                )
                for child in value:
                    if isinstance(child, (dict, list)) and has_array_children:
                        visit(child, array_path)
                    else:
                        normalized = canonical_value(child)
                        node = node_by_path.get(current_path)
                        if normalized and node:
                            kind, canonical, display = normalized
                            values[node.id][(kind, canonical)] = display
                return
            normalized = canonical_value(value)
            node = node_by_path.get(current_path)
            if normalized and node:
                kind, canonical, display = normalized
                values[node.id][(kind, canonical)] = display

        for document in collection.find({}, batch_size=500):
            visit(document, "")

        with sqlite3.connect(values_path) as connection:
            connection.executemany(
                "INSERT OR IGNORE INTO path_values(path_id, type_tag, normalized_value, display_value) VALUES (?, ?, ?, ?)",
                [
                    (identifier, kind, normalized, display)
                    for identifier, entries in values.items()
                    for (kind, normalized), display in entries.items()
                ],
            )
            connection.executemany(
                "INSERT OR IGNORE INTO key_values(path_id, type_tag, normalized_value, display_value) VALUES (?, ?, ?, ?)",
                [
                    (identifier, kind, normalized, display)
                    for identifier, entries in keys.items()
                    for (kind, normalized), display in entries.items()
                ],
            )
            connection.commit()

        child_parents = {node.parent_id for node in nodes if node.parent_id}
        payload: dict[str, Any] = {}
        for node in nodes:
            if node.id not in child_parents:
                entries = values.get(node.id, {})
                numeric = [float(value) for (kind, value) in entries if kind == "number"]
                if entries and len(numeric) == len(entries):
                    profile = ValueProfile(
                        mode="range",
                        distinct_count=len(entries),
                        min=min(numeric),
                        max=max(numeric),
                    )
                elif entries:
                    profile = ValueProfile(
                        mode="enum" if len(entries) <= 256 else "exact",
                        distinct_count=len(entries),
                    )
                else:
                    profile = ValueProfile(mode="none")
                payload[node.id] = {"value_profile": profile.model_dump()}
            if node.key_profile:
                entries = keys.get(node.id, {})
                display_values = [display for display in entries.values()]
                numeric = [int(value) for (kind, value) in entries if kind == "number"]
                key_profile = node.key_profile.model_copy(deep=True)
                if entries:
                    key_profile.distinct_count = len(entries)
                    if len(numeric) == len(entries):
                        key_profile.min = min(numeric)
                        key_profile.max = max(numeric)
                        key_profile.values = sorted(numeric) if len(numeric) <= 256 else []
                    else:
                        key_profile.values = sorted(display_values) if len(entries) <= 256 else []
                    key_profile.mode = "enum" if len(entries) <= 256 else "exact"
                payload.setdefault(node.id, {})["key_profile"] = key_profile.model_dump()
        temporary = profile_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, profile_path)

    def _reference_stage(
        self, run: SchemaIndexRunView, staging: Path, nodes: list[SchemaIndexNode]
    ) -> list[SchemaReferenceEdge]:
        output = staging / "reference_edges.json"
        stage = self._stage(run, "reference_inference")
        if stage.status == "completed" and output.is_file():
            return [SchemaReferenceEdge.model_validate(item) for item in json.loads(output.read_text(encoding="utf-8"))]
        self._start_stage(run, stage, "Inferring strict ID references")
        value_sets = self._load_value_sets(staging / "values.sqlite3")
        node_by_id = {node.id: node for node in nodes}
        references = self._declared_references(run.database_id, node_by_id, value_sets)
        declared_pairs = {(item.source_id, item.target_id) for item in references}
        source_nodes = [
            node for node in nodes
            if node.id in value_sets and self._is_source_id(node) and normalize_text(last_segment(node.path)) != "id"
        ]
        target_nodes = [node for node in nodes if node.id in value_sets and self._is_target_id(node)]
        for index, source in enumerate(source_nodes, start=1):
            source_values = value_sets[source.id]
            qualified: list[tuple[int, float, SchemaIndexNode, int]] = []
            for target in target_nodes:
                if target.id == source.id or not self._types_compatible(source.types, target.types):
                    continue
                name_score = self._reference_name_score(source, target)
                if name_score <= 0:
                    continue
                matched = len(source_values & value_sets[target.id])
                overlap = matched / len(source_values) if source_values else 0
                if overlap >= 0.9:
                    qualified.append((name_score, overlap, target, matched))
            if qualified:
                qualified.sort(key=lambda item: (item[0], item[1]), reverse=True)
                best = qualified[0]
                tied = [item for item in qualified if item[:2] == best[:2]]
                if len(tied) == 1:
                    if (source.id, best[2].id) in declared_pairs:
                        continue
                    references.append(
                        SchemaReferenceEdge(
                            source_id=source.id,
                            target_id=best[2].id,
                            source_distinct_count=len(source_values),
                            matched_distinct_count=best[3],
                            overlap=round(best[1], 6),
                            inferred_by="strict_inference",
                        )
                    )
            stage.progress = index / max(1, len(source_nodes))
            if index % 20 == 0:
                stage.summary = f"Checked {index}/{len(source_nodes)} ID-like source paths"
                self.store.save(run)
                self._check_control(run.run_id)
        output.write_text(
            json.dumps([item.model_dump() for item in references], indent=2), encoding="utf-8"
        )
        run.reference_edge_count = len(references)
        self._complete_stage(
            run,
            stage,
            f"{len(references)} unambiguous references inferred",
            {"reference_edge_count": len(references), "minimum_overlap": 0.9},
        )
        self._check_control(run.run_id)
        return references

    def _declared_references(
        self,
        database_id: str,
        nodes: dict[str, SchemaIndexNode],
        value_sets: dict[str, set[tuple[str, str]]],
    ) -> list[SchemaReferenceEdge]:
        """Load optional curated edges without confusing schema $ref metadata for data joins."""
        candidates = [
            self.settings.schema_tree_dir / f"references_{database_id}.json",
            self.settings.schema_tree_dir / "declared_references.json",
        ]
        payload: Any = None
        for path in candidates:
            if path.is_file():
                payload = json.loads(path.read_text(encoding="utf-8"))
                if path.name == "declared_references.json":
                    payload = payload.get(database_id, []) if isinstance(payload, dict) else []
                break
        if not isinstance(payload, list):
            return []
        result: list[SchemaReferenceEdge] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            source_id = str(item.get("source_id") or "")
            target_id = str(item.get("target_id") or "")
            if source_id not in nodes or target_id not in nodes:
                continue
            source_values = value_sets.get(source_id, set())
            target_values = value_sets.get(target_id, set())
            matched = len(source_values & target_values)
            overlap = matched / len(source_values) if source_values else 0
            result.append(
                SchemaReferenceEdge(
                    source_id=source_id,
                    target_id=target_id,
                    source_distinct_count=len(source_values),
                    matched_distinct_count=matched,
                    overlap=round(overlap, 6),
                    inferred_by="declared",
                )
            )
        return result

    def _embedding_stage(
        self, run: SchemaIndexRunView, staging: Path, nodes: list[SchemaIndexNode]
    ) -> None:
        stage = self._stage(run, "embedding_generation")
        embeddings_path = staging / "embeddings.sqlite3"
        self._initialize_embeddings(embeddings_path)
        expected: list[tuple[str, str]] = []
        for node in nodes:
            expected.append((f"{node.id}#search", node.search_text))
            expected.append((f"{node.id}#context", node.context_text or node.collection))
        with sqlite3.connect(embeddings_path) as connection:
            existing = {
                row[0] for row in connection.execute("SELECT embedding_ref FROM embeddings")
            }
        pending = [item for item in expected if item[0] not in existing]
        if stage.status == "completed" and not pending:
            run.embedding_count = len(expected)
            self.store.save(run)
            return
        self._start_stage(run, stage, f"Generating {len(pending)} remaining embeddings")
        batch_size = self.settings.embedding_batch_size
        for offset in range(0, len(pending), batch_size):
            batch = pending[offset : offset + batch_size]
            vectors = self.embedding_client.embed([text for _, text in batch])
            dimensions = len(vectors[0])
            with sqlite3.connect(embeddings_path) as connection:
                connection.executemany(
                    "INSERT OR REPLACE INTO embeddings(embedding_ref, model, text_hash, dimensions, vector) VALUES (?, ?, ?, ?, ?)",
                    [
                        (
                            reference,
                            self.settings.embedding_model,
                            hashlib.sha256(text.encode("utf-8")).hexdigest(),
                            dimensions,
                            struct.pack(f"<{dimensions}f", *vector),
                        )
                        for (reference, text), vector in zip(batch, vectors, strict=True)
                    ],
                )
                connection.commit()
            completed = min(len(pending), offset + len(batch))
            stage.progress = completed / max(1, len(pending))
            stage.summary = f"Embedded {completed}/{len(pending)} remaining texts"
            self.store.save(run)
            self._check_control(run.run_id)
        run.embedding_count = len(expected)
        self._complete_stage(
            run,
            stage,
            f"{run.embedding_count} non-empty embeddings persisted",
            {"embedding_count": run.embedding_count, "model": self.settings.embedding_model},
        )

    def _validation_stage(
        self,
        run: SchemaIndexRunView,
        staging: Path,
        nodes: list[SchemaIndexNode],
        references: list[SchemaReferenceEdge],
    ) -> None:
        stage = self._stage(run, "artifact_validation")
        self._start_stage(run, stage, "Validating complete, reopenable artifacts")
        profiled_path = staging / "nodes.profiled.jsonl"
        values_path = staging / "values.sqlite3"
        embeddings_path = staging / "embeddings.sqlite3"
        loaded = self._read_nodes(profiled_path)
        if not loaded or len(loaded) != len(nodes):
            raise RuntimeError("Persisted node artifact is empty or incomplete")
        with sqlite3.connect(embeddings_path) as connection:
            embedding_count = int(connection.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0])
            dimensions = {
                int(row[0]) for row in connection.execute("SELECT DISTINCT dimensions FROM embeddings")
            }
        if embedding_count != len(nodes) * 2 or len(dimensions) != 1 or next(iter(dimensions), 0) <= 0:
            raise RuntimeError("Embedding artifact is empty, incomplete, or dimensionally inconsistent")
        with sqlite3.connect(values_path) as connection:
            connection.execute("SELECT COUNT(*) FROM path_values").fetchone()
            connection.execute("PRAGMA integrity_check").fetchone()
        manifest = {
            "format": "tend-schema-index/v1",
            "index_id": run.index_id,
            "database_id": run.database_id,
            "schema_hash": run.schema_hash,
            "index_version": "1",
            "normalizer_version": "1",
            "embedding_model": self.settings.embedding_model,
            "embedding_dimensions": next(iter(dimensions)),
            "created_at": schema_now().isoformat(),
            "node_count": len(nodes),
            "collection_count": run.collection_count,
            "value_count": run.value_count,
            "dynamic_key_count": run.dynamic_key_count,
            "array_path_count": run.array_path_count,
            "reference_edge_count": len(references),
            "embedding_count": embedding_count,
            "files": {
                name: hashlib.sha256((staging / name).read_bytes()).hexdigest()
                for name in [
                    "nodes.profiled.jsonl",
                    "values.sqlite3",
                    "embeddings.sqlite3",
                    "reference_edges.json",
                ]
            },
        }
        (staging / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        self._complete_stage(
            run,
            stage,
            "All index artifacts passed completeness checks",
            {"manifest": manifest},
        )

    def _check_control(self, run_id: str) -> None:
        current = self._require(run_id)
        if current.status == "cancelled":
            raise BuildCancelled()
        if current.status == "pausing":
            current.status = "paused"
            for stage in current.stages:
                if stage.status == "running":
                    stage.status = "paused"
                    stage.summary = "Paused after the last verified checkpoint"
            self.store.save(current)
            raise BuildPaused()

    @staticmethod
    def _initialize_values(path: Path) -> None:
        with sqlite3.connect(path) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS path_values (
                    path_id TEXT NOT NULL,
                    type_tag TEXT NOT NULL,
                    normalized_value TEXT NOT NULL,
                    display_value TEXT NOT NULL,
                    PRIMARY KEY(path_id, type_tag, normalized_value)
                );
                CREATE INDEX IF NOT EXISTS idx_path_values_lookup
                    ON path_values(path_id, normalized_value);
                CREATE TABLE IF NOT EXISTS key_values (
                    path_id TEXT NOT NULL,
                    type_tag TEXT NOT NULL,
                    normalized_value TEXT NOT NULL,
                    display_value TEXT NOT NULL,
                    PRIMARY KEY(path_id, type_tag, normalized_value)
                );
                CREATE INDEX IF NOT EXISTS idx_key_values_lookup
                    ON key_values(path_id, normalized_value);
                """
            )

    @staticmethod
    def _initialize_embeddings(path: Path) -> None:
        with sqlite3.connect(path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS embeddings (
                    embedding_ref TEXT PRIMARY KEY,
                    model TEXT NOT NULL,
                    text_hash TEXT NOT NULL,
                    dimensions INTEGER NOT NULL,
                    vector BLOB NOT NULL
                )
                """
            )

    @staticmethod
    def _load_value_sets(path: Path) -> dict[str, set[tuple[str, str]]]:
        result: dict[str, set[tuple[str, str]]] = defaultdict(set)
        with sqlite3.connect(path) as connection:
            for identifier, kind, value in connection.execute(
                "SELECT path_id, type_tag, normalized_value FROM path_values"
            ):
                result[str(identifier)].add((str(kind), str(value)))
        return result

    @staticmethod
    def _is_source_id(node: SchemaIndexNode) -> bool:
        tokens = normalize_text(last_segment(node.path)).split()
        return bool(tokens and tokens[-1] in {"id", "ref", "reference"})

    @staticmethod
    def _is_target_id(node: SchemaIndexNode) -> bool:
        tokens = normalize_text(last_segment(node.path)).split()
        return bool(tokens and tokens[-1] == "id")

    @staticmethod
    def _types_compatible(left: list[str], right: list[str]) -> bool:
        def family(values: list[str]) -> set[str]:
            result = set()
            for value in values:
                lowered = value.lower()
                result.add("number" if lowered in {"integer", "number", "int", "long", "double", "decimal"} else lowered)
            return result
        return bool(family(left) & family(right))

    @staticmethod
    def _reference_name_score(source: SchemaIndexNode, target: SchemaIndexNode) -> int:
        source_tokens = set(normalize_text(last_segment(source.path)).split()) - {"id", "ref", "reference"}
        target_tokens = set(normalize_text(f"{target.collection} {target.context_text} {last_segment(target.path)}").split()) - {"id"}
        if not source_tokens:
            return 0
        return len(source_tokens & target_tokens)

    @staticmethod
    def _write_nodes(path: Path, nodes: list[SchemaIndexNode]) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            for node in nodes:
                stream.write(node.model_dump_json() + "\n")
        os.replace(temporary, path)

    @staticmethod
    def _read_nodes(path: Path) -> list[SchemaIndexNode]:
        return [
            SchemaIndexNode.model_validate_json(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line
        ]

    @staticmethod
    def _stage(run: SchemaIndexRunView, name: str) -> IndexStageTrace:
        return next(item for item in run.stages if item.stage == name)

    def _start_stage(self, run: SchemaIndexRunView, stage: IndexStageTrace, summary: str) -> None:
        stage.status = "running"
        stage.started_at = stage.started_at or schema_now()
        stage.summary = summary
        stage.error = None
        run.status = "running"
        self.store.save(run)

    def _complete_stage(
        self,
        run: SchemaIndexRunView,
        stage: IndexStageTrace,
        summary: str,
        artifact: dict[str, Any],
    ) -> None:
        stage.status = "completed"
        stage.progress = 1
        stage.completed_at = schema_now()
        stage.summary = summary
        stage.artifact = artifact
        self.store.save(run)

    def _require(self, run_id: str) -> SchemaIndexRunView:
        run = self.store.get(run_id)
        if run is None:
            raise KeyError(f"Schema index run not found: {run_id}")
        return run
