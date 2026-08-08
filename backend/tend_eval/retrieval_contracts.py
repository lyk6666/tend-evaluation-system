from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from .schema_contracts import SchemaReferenceEdge


def retrieval_now() -> datetime:
    return datetime.now(UTC)


PRUNING_STAGE_NAMES = [
    "request_compilation",
    "local_scoring",
    "relationship_scoring",
    "combination_search",
    "schema_pruning",
]


class PruningConfig(BaseModel):
    primary_top_k: int = Field(default=10, ge=1, le=50)
    supporting_top_k: int = Field(default=5, ge=1, le=50)
    beam_width: int = Field(default=20, ge=1, le=500)
    final_top_k: int = Field(default=3, ge=1, le=10)
    alternative_margin: float = Field(default=0.05, ge=0, le=1)
    local_weight: float = Field(default=0.75, gt=0, le=1)
    relationship_weight: float = Field(default=0.25, ge=0, lt=1)


class SchemaPruningRunCreate(BaseModel):
    bundle_run_id: str
    index_id: str
    config: PruningConfig = Field(default_factory=PruningConfig)


class PruningStageTrace(BaseModel):
    stage: str
    status: Literal["pending", "running", "completed", "failed"] = "pending"
    progress: float = Field(default=0, ge=0, le=1)
    summary: str = ""
    artifact: Any = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: float | None = None
    error: str | None = None


class SearchRequest(BaseModel):
    target_id: str
    kind: str
    role: str
    query_text: str
    context_text: str
    expected_types: list[str] = Field(default_factory=list)
    value_constraints: list[dict[str, Any]] = Field(default_factory=list)


class CandidateSignals(BaseModel):
    lexical: float
    semantic: float
    name: float
    context: float | None = None
    type: float | None = None
    value: float | None = None
    exact_contains: bool | None = None
    range_contains: bool | None = None


class PathCandidate(BaseModel):
    target_id: str
    path_id: str
    collection: str
    path: str
    score: float
    signals: CandidateSignals
    binding: dict[str, Any] | None = None


class RelationshipEvaluation(BaseModel):
    relation_index: int
    relation_type: str
    source_target_id: str
    target_target_id: str
    source_path_id: str
    target_path_id: str
    score: float
    distance: float | None = None
    common_ancestor_id: str | None = None
    connector_node_ids: list[str] = Field(default_factory=list)
    reference_edges: list[SchemaReferenceEdge] = Field(default_factory=list)
    resolved: bool = False


class CandidateCombination(BaseModel):
    rank: int = 0
    selections: dict[str, str] = Field(default_factory=dict)
    local_score: float
    relationship_score: float
    final_score: float
    relationship_evaluations: list[RelationshipEvaluation] = Field(default_factory=list)
    connector_node_ids: list[str] = Field(default_factory=list)
    reference_edges: list[SchemaReferenceEdge] = Field(default_factory=list)


class PrunedNode(BaseModel):
    id: str
    collection: str
    path: str
    parent_id: str | None
    types: list[str] = Field(default_factory=list)
    role: Literal["target", "connector", "ancestor"]
    target_ids: list[str] = Field(default_factory=list)
    binding: dict[str, Any] | None = None


class PrunedSchema(BaseModel):
    combination_rank: int
    score: float
    nodes: list[PrunedNode] = Field(default_factory=list)
    reference_edges: list[SchemaReferenceEdge] = Field(default_factory=list)


class SchemaPruningRunView(BaseModel):
    run_id: str
    bundle_run_id: str
    index_id: str
    database_id: str
    status: Literal["created", "running", "completed", "failed"] = "created"
    created_at: datetime = Field(default_factory=retrieval_now)
    updated_at: datetime = Field(default_factory=retrieval_now)
    config: PruningConfig
    stages: list[PruningStageTrace] = Field(default_factory=list)
    search_requests: list[SearchRequest] = Field(default_factory=list)
    candidates_by_target: dict[str, list[PathCandidate]] = Field(default_factory=dict)
    relationship_evaluations: list[RelationshipEvaluation] = Field(default_factory=list)
    combinations: list[CandidateCombination] = Field(default_factory=list)
    pruned_schemas: list[PrunedSchema] = Field(default_factory=list)
    unresolved_target_ids: list[str] = Field(default_factory=list)
    failure: str | None = None

