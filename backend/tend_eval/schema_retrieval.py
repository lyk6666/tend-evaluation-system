from __future__ import annotations

import heapq
import json
import math
import re
import sqlite3
import struct
import threading
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from .config import Settings
from .contracts import RetrievalBundle
from .retrieval_contracts import (
    PRUNING_STAGE_NAMES,
    CandidateCombination,
    CandidateSignals,
    PathCandidate,
    PrunedNode,
    PrunedSchema,
    PruningStageTrace,
    RelationshipEvaluation,
    SchemaPruningRunCreate,
    SchemaPruningRunView,
    SearchRequest,
    retrieval_now,
)
from .retrieval_store import SchemaPruningRunStore
from .schema_contracts import SchemaIndexNode, SchemaReferenceEdge
from .schema_indexing import EmbeddingClient, canonical_value, normalize_text
from .schema_store import SchemaIndexRunStore
from .store import AnchorRunStore


TOKEN_RE = re.compile(r"[a-z]+|\d+")
NUMBER_TYPES = {"int", "integer", "long", "double", "number", "decimal", "float"}
STRING_TYPES = {"string", "text"}
DATE_TYPES = {"date", "datetime", "timestamp"}
EXACT_OPERATORS = {"=", "==", "eq", "equals", "in", "is"}
RANGE_OPERATORS = {">", ">=", "<", "<=", "gt", "gte", "lt", "lte", "between"}


class SchemaPruningManager:
    """Retrieve and jointly ground RetrievalBundle targets against one completed index."""

    def __init__(
        self,
        settings: Settings,
        store: SchemaPruningRunStore,
        anchor_store: AnchorRunStore,
        index_store: SchemaIndexRunStore,
        *,
        embedding_client: EmbeddingClient | None = None,
    ):
        self.settings = settings
        self.store = store
        self.anchor_store = anchor_store
        self.index_store = index_store
        self.embedding_client = embedding_client or EmbeddingClient(settings)
        self._threads: dict[str, threading.Thread] = {}
        self._locks: dict[str, threading.Lock] = {}

    def create(self, request: SchemaPruningRunCreate) -> SchemaPruningRunView:
        bundle_run = self.anchor_store.get(request.bundle_run_id)
        if bundle_run is None or bundle_run.status != "completed" or bundle_run.retrieval_bundle is None:
            raise ValueError("Select a completed RetrievalBundle run")
        index_run = self.index_store.get(request.index_id)
        if index_run is None or index_run.status != "completed" or not index_run.artifact_dir:
            raise ValueError("Select a completed schema index")
        artifact_dir = Path(index_run.artifact_dir)
        if not (artifact_dir / "READY").is_file():
            raise ValueError("The selected schema index has no validated READY artifact")
        if not self.settings.embedding_ready:
            raise RuntimeError(
                "Configure TEND_EVAL_EMBEDDING_API_KEY or OPENAI_API_KEY before retrieval"
            )
        if abs(request.config.local_weight + request.config.relationship_weight - 1) > 1e-6:
            raise ValueError("local_weight and relationship_weight must sum to 1")
        identifier = uuid.uuid4().hex
        run = SchemaPruningRunView(
            run_id=identifier,
            bundle_run_id=request.bundle_run_id,
            index_id=request.index_id,
            database_id=index_run.database_id,
            config=request.config,
            stages=[PruningStageTrace(stage=name) for name in PRUNING_STAGE_NAMES],
        )
        self.store.save(run)
        return run

    def start(self, run_id: str) -> SchemaPruningRunView:
        run = self._require(run_id)
        if run.status in {"completed", "running"}:
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
        try:
            run = self._require(run_id)
            anchor_run = self.anchor_store.get(run.bundle_run_id)
            index_run = self.index_store.get(run.index_id)
            if anchor_run is None or anchor_run.retrieval_bundle is None:
                raise RuntimeError("RetrievalBundle disappeared before retrieval started")
            if index_run is None or not index_run.artifact_dir:
                raise RuntimeError("Schema index disappeared before retrieval started")
            artifact_dir = Path(index_run.artifact_dir)
            bundle = anchor_run.retrieval_bundle
            nodes = self._load_nodes(artifact_dir / "nodes.profiled.jsonl")
            references = self._load_references(artifact_dir / "reference_edges.json")
            if not nodes:
                raise RuntimeError("The selected index contains no schema nodes")

            requests = self._run_stage(
                run,
                "request_compilation",
                lambda: self._compile_requests(bundle),
                lambda value: (
                    f"Compiled {len(value)} minimal search requests",
                    [item.model_dump(mode="json") for item in value],
                ),
            )
            run.search_requests = requests
            self.store.save(run)

            candidates = self._run_stage(
                run,
                "local_scoring",
                lambda: self._score_candidates(run, requests, nodes, artifact_dir),
                lambda value: (
                    f"Scored all {len(nodes)} paths for {len(value)} targets",
                    {
                        target_id: [item.model_dump(mode="json") for item in items]
                        for target_id, items in value.items()
                    },
                ),
            )
            run.candidates_by_target = candidates
            run.unresolved_target_ids = [
                request.target_id for request in requests if not candidates.get(request.target_id)
            ]
            self.store.save(run)

            graph = SchemaGraph(nodes, references)
            relationship_matrix = self._run_stage(
                run,
                "relationship_scoring",
                lambda: self._score_relationships(bundle, candidates, graph),
                lambda value: (
                    f"Evaluated {len(value)} candidate-pair relationships",
                    [item.model_dump(mode="json") for item in value],
                ),
            )
            run.relationship_evaluations = relationship_matrix
            self.store.save(run)

            combinations = self._run_stage(
                run,
                "combination_search",
                lambda: self._combine(run, requests, candidates, relationship_matrix),
                lambda value: (
                    f"Retained {len(value)} joint grounding alternative(s)",
                    [item.model_dump(mode="json") for item in value],
                ),
            )
            run.combinations = combinations
            self.store.save(run)

            pruned = self._run_stage(
                run,
                "schema_pruning",
                lambda: self._prune(combinations, candidates, graph),
                lambda value: (
                    f"Built {len(value)} connected pruned schema alternative(s)",
                    [item.model_dump(mode="json") for item in value],
                ),
            )
            run.pruned_schemas = pruned
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
                        stage.completed_at = retrieval_now()
                self.store.save(current)
        finally:
            lock.release()

    def _compile_requests(self, bundle: RetrievalBundle) -> list[SearchRequest]:
        requests: list[SearchRequest] = []
        for target in bundle.targets:
            query_parts = self._deduplicate([target.canonical, *target.aliases])
            requests.append(
                SearchRequest(
                    target_id=target.id,
                    kind=str(target.kind),
                    role=target.role,
                    query_text=" ".join(query_parts),
                    context_text=" ".join(self._deduplicate(target.parent_hints)),
                    expected_types=target.expected_types,
                    value_constraints=[
                        item.model_dump(mode="json")
                        for item in bundle.value_constraints
                        if target.id in item.target_ids
                    ],
                )
            )
        return requests

    def _score_candidates(
        self,
        run: SchemaPruningRunView,
        requests: list[SearchRequest],
        nodes: list[SchemaIndexNode],
        artifact_dir: Path,
    ) -> dict[str, list[PathCandidate]]:
        children: dict[str, int] = defaultdict(int)
        for node in nodes:
            if node.parent_id:
                children[node.parent_id] += 1
        query_texts: list[str] = []
        query_refs: list[tuple[str, str]] = []
        for request in requests:
            query_refs.append((request.target_id, "search"))
            query_texts.append(request.query_text)
            if request.context_text:
                query_refs.append((request.target_id, "context"))
                query_texts.append(request.context_text)
        vectors = self.embedding_client.embed(query_texts)
        query_vectors = {key: vector for key, vector in zip(query_refs, vectors, strict=True)}
        node_vectors = self._load_embeddings(artifact_dir / "embeddings.sqlite3")
        values_path = artifact_dir / "values.sqlite3"
        result: dict[str, list[PathCandidate]] = {}
        total = max(1, len(requests))
        stage = self._stage(run, "local_scoring")
        for position, request in enumerate(requests, start=1):
            eligible = [node for node in nodes if self._eligible(request, node, children)]
            scored: list[PathCandidate] = []
            for node in eligible:
                lexical = token_dice(request.query_text, node.search_text)
                semantic = cosine(
                    query_vectors[(request.target_id, "search")],
                    node_vectors.get(f"{node.id}#search", []),
                )
                semantic = (semantic + 1) / 2
                name = 0.4 * lexical + 0.6 * semantic
                context = None
                if request.context_text:
                    context_lexical = token_dice(request.context_text, node.context_text)
                    context_semantic = cosine(
                        query_vectors[(request.target_id, "context")],
                        node_vectors.get(f"{node.id}#context", []),
                    )
                    context = 0.4 * context_lexical + 0.6 * ((context_semantic + 1) / 2)
                type_score = self._type_score(request.expected_types, node.types)
                value_score, exact_contains, range_contains = self._value_score(
                    request.value_constraints, node, values_path
                )
                active: list[tuple[float, float]] = [(0.5, name)]
                if context is not None:
                    active.append((0.2, context))
                if type_score is not None:
                    active.append((0.1, type_score))
                if value_score is not None:
                    active.append((0.2, value_score))
                denominator = sum(weight for weight, _ in active)
                score = sum(weight * value for weight, value in active) / denominator
                scored.append(
                    PathCandidate(
                        target_id=request.target_id,
                        path_id=node.id,
                        collection=node.collection,
                        path=node.path,
                        score=round(score, 6),
                        signals=CandidateSignals(
                            lexical=round(lexical, 6),
                            semantic=round(semantic, 6),
                            name=round(name, 6),
                            context=round(context, 6) if context is not None else None,
                            type=round(type_score, 6) if type_score is not None else None,
                            value=round(value_score, 6) if value_score is not None else None,
                            exact_contains=exact_contains,
                            range_contains=range_contains,
                        ),
                        binding=self._binding(node, request.value_constraints),
                    )
                )
            scored.sort(key=lambda item: (-item.score, item.path_id))
            limit = (
                run.config.primary_top_k
                if request.role == "primary"
                else run.config.supporting_top_k
            )
            result[request.target_id] = scored[:limit]
            stage.progress = position / total
            stage.summary = f"Scored target {position}/{len(requests)}"
            self.store.save(run)
        return result

    def _score_relationships(
        self,
        bundle: RetrievalBundle,
        candidates: dict[str, list[PathCandidate]],
        graph: "SchemaGraph",
    ) -> list[RelationshipEvaluation]:
        result: list[RelationshipEvaluation] = []
        for relation_index, relation in enumerate(bundle.relations):
            for source in candidates.get(relation.source, []):
                for target in candidates.get(relation.target, []):
                    result.append(
                        graph.evaluate(
                            relation_index,
                            relation.type,
                            relation.source,
                            relation.target,
                            source.path_id,
                            target.path_id,
                        )
                    )
        return result

    def _combine(
        self,
        run: SchemaPruningRunView,
        requests: list[SearchRequest],
        candidates: dict[str, list[PathCandidate]],
        relationships: list[RelationshipEvaluation],
    ) -> list[CandidateCombination]:
        candidate_lookup = {
            (candidate.target_id, candidate.path_id): candidate
            for items in candidates.values()
            for candidate in items
        }
        relation_lookup: dict[tuple[str, str, str, str], list[RelationshipEvaluation]] = defaultdict(list)
        for item in relationships:
            relation_lookup[
                (item.source_target_id, item.target_target_id, item.source_path_id, item.target_path_id)
            ].append(item)

        beam: list[dict[str, str]] = [{}]
        request_order = sorted(
            requests,
            key=lambda item: (item.role != "primary", len(candidates.get(item.target_id, []))),
        )
        for request in request_order:
            expanded: list[tuple[float, dict[str, str]]] = []
            for partial in beam:
                for candidate in candidates.get(request.target_id, []):
                    selection = {**partial, request.target_id: candidate.path_id}
                    score = self._partial_score(selection, candidate_lookup, relation_lookup)
                    expanded.append((score, selection))
            expanded.sort(key=lambda item: (-item[0], sorted(item[1].items())))
            beam = [selection for _, selection in expanded[: run.config.beam_width]]
            if not beam:
                return []

        complete = [
            self._combination(selection, candidate_lookup, relation_lookup, run)
            for selection in beam
        ]
        complete.sort(key=lambda item: (-item.final_score, sorted(item.selections.items())))
        if not complete:
            return []
        best = complete[0].final_score
        retained = [
            item for item in complete
            if best - item.final_score <= run.config.alternative_margin
        ][: run.config.final_top_k]
        for rank, item in enumerate(retained, start=1):
            item.rank = rank
        return retained

    @staticmethod
    def _partial_score(
        selection: dict[str, str],
        candidates: dict[tuple[str, str], PathCandidate],
        relations: dict[tuple[str, str, str, str], list[RelationshipEvaluation]],
    ) -> float:
        local = [candidates[(target_id, path_id)].score for target_id, path_id in selection.items()]
        pairwise: list[float] = []
        for (source_id, target_id, source_path, target_path), evaluations in relations.items():
            if selection.get(source_id) == source_path and selection.get(target_id) == target_path:
                pairwise.extend(item.score for item in evaluations)
        local_score = sum(local) / len(local) if local else 0
        relationship_score = sum(pairwise) / len(pairwise) if pairwise else 0
        return 0.75 * local_score + 0.25 * relationship_score

    @staticmethod
    def _combination(
        selection: dict[str, str],
        candidates: dict[tuple[str, str], PathCandidate],
        relations: dict[tuple[str, str, str, str], list[RelationshipEvaluation]],
        run: SchemaPruningRunView,
    ) -> CandidateCombination:
        local_values = [
            candidates[(target_id, path_id)].score for target_id, path_id in selection.items()
        ]
        evaluations: list[RelationshipEvaluation] = []
        for (source_id, target_id, source_path, target_path), items in relations.items():
            if selection.get(source_id) == source_path and selection.get(target_id) == target_path:
                evaluations.extend(items)
        local_score = sum(local_values) / len(local_values) if local_values else 0
        relationship_score = (
            sum(item.score for item in evaluations) / len(evaluations) if evaluations else 0
        )
        final_score = (
            run.config.local_weight * local_score
            + run.config.relationship_weight * relationship_score
        )
        connectors = sorted({node for item in evaluations for node in item.connector_node_ids})
        references = deduplicate_references(
            edge for item in evaluations for edge in item.reference_edges
        )
        return CandidateCombination(
            selections=selection,
            local_score=round(local_score, 6),
            relationship_score=round(relationship_score, 6),
            final_score=round(final_score, 6),
            relationship_evaluations=evaluations,
            connector_node_ids=connectors,
            reference_edges=references,
        )

    def _prune(
        self,
        combinations: list[CandidateCombination],
        candidates: dict[str, list[PathCandidate]],
        graph: "SchemaGraph",
    ) -> list[PrunedSchema]:
        candidate_lookup = {
            (candidate.target_id, candidate.path_id): candidate
            for items in candidates.values()
            for candidate in items
        }
        result: list[PrunedSchema] = []
        for combination in combinations:
            targets_by_node: dict[str, list[str]] = defaultdict(list)
            bindings_by_node: dict[str, dict[str, Any]] = {}
            for target_id, path_id in combination.selections.items():
                targets_by_node[path_id].append(target_id)
                candidate = candidate_lookup[(target_id, path_id)]
                if candidate.binding:
                    bindings_by_node[path_id] = candidate.binding
            connector_ids = set(combination.connector_node_ids)
            included = set(targets_by_node) | connector_ids
            for node_id in list(included):
                included.update(graph.ancestors(node_id))
            pruned_nodes: list[PrunedNode] = []
            for node_id in sorted(included):
                node = graph.nodes[node_id]
                role = (
                    "target"
                    if node_id in targets_by_node
                    else "connector"
                    if node_id in connector_ids
                    else "ancestor"
                )
                pruned_nodes.append(
                    PrunedNode(
                        id=node.id,
                        collection=node.collection,
                        path=node.path,
                        parent_id=node.parent_id,
                        types=node.types,
                        role=role,
                        target_ids=targets_by_node.get(node_id, []),
                        binding=bindings_by_node.get(node_id),
                    )
                )
            result.append(
                PrunedSchema(
                    combination_rank=combination.rank,
                    score=combination.final_score,
                    nodes=pruned_nodes,
                    reference_edges=combination.reference_edges,
                )
            )
        return result

    @staticmethod
    def _eligible(
        request: SearchRequest, node: SchemaIndexNode, children: dict[str, int]
    ) -> bool:
        if not node.path:
            return request.kind == "entity"
        if request.kind == "entity":
            return children.get(node.id, 0) > 0
        return children.get(node.id, 0) == 0 or node.key_profile is not None

    @staticmethod
    def _type_score(expected: list[str], actual: list[str]) -> float | None:
        if not expected:
            return None
        if not actual:
            return 0.5
        expected_normalized = {item.lower() for item in expected}
        actual_normalized = {item.lower() for item in actual}
        if expected_normalized & actual_normalized:
            return 1.0
        if type_family(expected_normalized) & type_family(actual_normalized):
            return 1.0
        return 0.0

    def _value_score(
        self,
        constraints: list[dict[str, Any]],
        node: SchemaIndexNode,
        values_path: Path,
    ) -> tuple[float | None, bool | None, bool | None]:
        applicable = [item for item in constraints if item.get("value") is not None]
        if not applicable:
            return None, None, None
        scores: list[float] = []
        exact_results: list[bool] = []
        range_results: list[bool] = []
        for constraint in applicable:
            value = constraint.get("value")
            operator = str(constraint.get("operator") or "=").lower()
            exact = self._contains_exact(values_path, node, value)
            exact_results.append(exact)
            if operator in EXACT_OPERATORS or operator not in RANGE_OPERATORS:
                scores.append(1.0 if exact else 0.0)
                continue
            within = self._range_compatible(node, operator, value)
            range_results.append(within)
            scores.append(0.8 if within else 0.0)
        return (
            sum(scores) / len(scores),
            all(exact_results) if exact_results else None,
            all(range_results) if range_results else None,
        )

    @staticmethod
    def _contains_exact(values_path: Path, node: SchemaIndexNode, value: Any) -> bool:
        normalized = canonical_value(value)
        if normalized is None:
            return False
        _kind, normalized_value, _display = normalized
        table = "key_values" if node.key_profile is not None else "path_values"
        with sqlite3.connect(values_path) as connection:
            return connection.execute(
                f"SELECT 1 FROM {table} WHERE path_id = ? AND normalized_value = ? LIMIT 1",
                (node.id, normalized_value),
            ).fetchone() is not None

    @staticmethod
    def _range_compatible(node: SchemaIndexNode, operator: str, value: Any) -> bool:
        profile = node.key_profile or node.value_profile
        if profile is None or profile.min is None or profile.max is None:
            return False
        try:
            minimum = float(profile.min)
            maximum = float(profile.max)
            query = float(value)
        except (TypeError, ValueError):
            return False
        if operator in {">", ">=", "gt", "gte"}:
            return maximum >= query
        if operator in {"<", "<=", "lt", "lte"}:
            return minimum <= query
        return minimum <= query <= maximum

    @staticmethod
    def _binding(
        node: SchemaIndexNode, constraints: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        if node.key_profile is None or not constraints:
            return None
        values = [item.get("value") for item in constraints if item.get("value") is not None]
        return {"dynamic_key_values": values} if values else None

    @staticmethod
    def _load_nodes(path: Path) -> list[SchemaIndexNode]:
        return [
            SchemaIndexNode.model_validate_json(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line
        ]

    @staticmethod
    def _load_references(path: Path) -> list[SchemaReferenceEdge]:
        if not path.is_file():
            return []
        return [
            SchemaReferenceEdge.model_validate(item)
            for item in json.loads(path.read_text(encoding="utf-8"))
        ]

    @staticmethod
    def _load_embeddings(path: Path) -> dict[str, list[float]]:
        result: dict[str, list[float]] = {}
        with sqlite3.connect(path) as connection:
            for reference, dimensions, blob in connection.execute(
                "SELECT embedding_ref, dimensions, vector FROM embeddings"
            ):
                result[str(reference)] = list(struct.unpack(f"<{int(dimensions)}f", blob))
        return result

    def _run_stage(self, run, name, operation, summarize):
        stage = self._stage(run, name)
        stage.status = "running"
        stage.started_at = retrieval_now()
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
            stage.completed_at = retrieval_now()
            stage.duration_ms = round((time.perf_counter() - started) * 1000, 3)
            self.store.save(run)

    @staticmethod
    def _stage(run: SchemaPruningRunView, name: str) -> PruningStageTrace:
        return next(item for item in run.stages if item.stage == name)

    @staticmethod
    def _deduplicate(values: Iterable[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            normalized = normalize_text(value)
            if normalized and normalized not in seen:
                seen.add(normalized)
                result.append(normalized)
        return result

    def _require(self, run_id: str) -> SchemaPruningRunView:
        run = self.store.get(run_id)
        if run is None:
            raise KeyError(run_id)
        return run


class SchemaGraph:
    def __init__(self, nodes: list[SchemaIndexNode], references: list[SchemaReferenceEdge]):
        self.nodes = {node.id: node for node in nodes}
        self.references = references
        self.adjacency: dict[str, list[tuple[str, float, SchemaReferenceEdge | None]]] = defaultdict(list)
        for node in nodes:
            if node.parent_id and node.parent_id in self.nodes:
                self.adjacency[node.id].append((node.parent_id, 1.0, None))
                self.adjacency[node.parent_id].append((node.id, 1.0, None))
        for edge in references:
            if edge.source_id in self.nodes and edge.target_id in self.nodes:
                self.adjacency[edge.source_id].append((edge.target_id, 2.0, edge))
                self.adjacency[edge.target_id].append((edge.source_id, 2.0, edge))

    def ancestors(self, node_id: str) -> list[str]:
        result: list[str] = []
        current = self.nodes.get(node_id)
        while current and current.parent_id:
            result.append(current.parent_id)
            current = self.nodes.get(current.parent_id)
        return result

    def evaluate(
        self,
        relation_index: int,
        relation_type: str,
        source_target_id: str,
        target_target_id: str,
        source_id: str,
        target_id: str,
    ) -> RelationshipEvaluation:
        if relation_type == "belongs_to":
            distance = self._ancestor_distance(source_id, target_id)
            score = 1 / distance if distance else 0
            connector = self._structural_between(source_id, target_id) if distance else []
            return self._evaluation(
                relation_index, relation_type, source_target_id, target_target_id,
                source_id, target_id, score, distance, connector=connector,
            )
        if relation_type == "scoped_with":
            lca, source_distance, target_distance = self._lca(source_id, target_id)
            valid = (
                self.nodes[source_id].collection == self.nodes[target_id].collection
                and lca is not None
                and bool(self.nodes[lca].path)
            )
            total = source_distance + target_distance
            score = min(1.0, 2 / total) if valid and total else 0
            connector = self._structural_between(source_id, target_id) if valid else []
            return self._evaluation(
                relation_index, relation_type, source_target_id, target_target_id,
                source_id, target_id, score, total if valid else None,
                common_ancestor=lca if valid else None, connector=connector,
            )
        # supports, derived_from, and requires_connection all need a confirmed schema route.
        cost, connector, edges = self.shortest_path(source_id, target_id)
        score = 2 / max(2.0, cost) if cost is not None else 0
        return self._evaluation(
            relation_index, relation_type, source_target_id, target_target_id,
            source_id, target_id, score, cost, connector=connector, edges=edges,
        )

    def shortest_path(
        self, source_id: str, target_id: str
    ) -> tuple[float | None, list[str], list[SchemaReferenceEdge]]:
        if source_id == target_id:
            return 0.0, [source_id], []
        distances = {source_id: 0.0}
        queue: list[tuple[float, str]] = [(0.0, source_id)]
        previous: dict[str, tuple[str, SchemaReferenceEdge | None]] = {}
        while queue:
            distance, current = heapq.heappop(queue)
            if distance != distances.get(current):
                continue
            if current == target_id:
                break
            for neighbor, weight, edge in self.adjacency.get(current, []):
                candidate = distance + weight
                if candidate < distances.get(neighbor, math.inf):
                    distances[neighbor] = candidate
                    previous[neighbor] = (current, edge)
                    heapq.heappush(queue, (candidate, neighbor))
        if target_id not in distances:
            return None, [], []
        route = [target_id]
        refs: list[SchemaReferenceEdge] = []
        current = target_id
        while current != source_id:
            parent, edge = previous[current]
            if edge is not None:
                refs.append(edge)
            route.append(parent)
            current = parent
        route.reverse()
        return distances[target_id], route, deduplicate_references(refs)

    def _ancestor_distance(self, source_id: str, target_id: str) -> int | None:
        distance = 0
        current = self.nodes.get(source_id)
        while current:
            if current.id == target_id:
                return distance or None
            if not current.parent_id:
                break
            current = self.nodes.get(current.parent_id)
            distance += 1
        return None

    def _lca(self, left_id: str, right_id: str) -> tuple[str | None, int, int]:
        left_chain = [left_id, *self.ancestors(left_id)]
        right_chain = [right_id, *self.ancestors(right_id)]
        right_positions = {node_id: index for index, node_id in enumerate(right_chain)}
        for left_distance, node_id in enumerate(left_chain):
            if node_id in right_positions:
                return node_id, left_distance, right_positions[node_id]
        return None, 0, 0

    def _structural_between(self, left_id: str, right_id: str) -> list[str]:
        lca, _, _ = self._lca(left_id, right_id)
        if lca is None:
            return []
        left: list[str] = []
        current = left_id
        while current != lca:
            left.append(current)
            current = self.nodes[current].parent_id or lca
        right: list[str] = []
        current = right_id
        while current != lca:
            right.append(current)
            current = self.nodes[current].parent_id or lca
        return [*left, lca, *reversed(right)]

    @staticmethod
    def _evaluation(
        relation_index: int,
        relation_type: str,
        source_target_id: str,
        target_target_id: str,
        source_id: str,
        target_id: str,
        score: float,
        distance: float | None,
        *,
        common_ancestor: str | None = None,
        connector: list[str] | None = None,
        edges: list[SchemaReferenceEdge] | None = None,
    ) -> RelationshipEvaluation:
        return RelationshipEvaluation(
            relation_index=relation_index,
            relation_type=relation_type,
            source_target_id=source_target_id,
            target_target_id=target_target_id,
            source_path_id=source_id,
            target_path_id=target_id,
            score=round(score, 6),
            distance=distance,
            common_ancestor_id=common_ancestor,
            connector_node_ids=connector or [],
            reference_edges=edges or [],
            resolved=score > 0,
        )


def token_dice(left: str, right: str) -> float:
    left_tokens = set(TOKEN_RE.findall(normalize_text(left)))
    right_tokens = set(TOKEN_RE.findall(normalize_text(right)))
    if not left_tokens or not right_tokens:
        return 0.0
    return 2 * len(left_tokens & right_tokens) / (len(left_tokens) + len(right_tokens))


def cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    denominator = math.sqrt(sum(value * value for value in left)) * math.sqrt(
        sum(value * value for value in right)
    )
    return numerator / denominator if denominator else 0.0


def type_family(values: set[str]) -> set[str]:
    result: set[str] = set()
    for value in values:
        if value in NUMBER_TYPES:
            result.add("number")
        elif value in STRING_TYPES:
            result.add("string")
        elif value in DATE_TYPES:
            result.add("date")
        else:
            result.add(value)
    return result


def deduplicate_references(
    edges: Iterable[SchemaReferenceEdge],
) -> list[SchemaReferenceEdge]:
    result: list[SchemaReferenceEdge] = []
    seen: set[tuple[str, str]] = set()
    for edge in edges:
        key = (edge.source_id, edge.target_id)
        if key not in seen:
            seen.add(key)
            result.append(edge)
    return result
