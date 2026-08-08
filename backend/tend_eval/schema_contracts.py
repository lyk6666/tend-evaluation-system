from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


def schema_now() -> datetime:
    return datetime.now(UTC)


IndexRunStatus = Literal[
    "created", "running", "pausing", "paused", "completed", "failed", "cancelled"
]
IndexStageStatus = Literal["pending", "running", "completed", "failed", "paused", "cancelled"]


INDEX_STAGE_NAMES = [
    "schema_traversal",
    "value_profiling",
    "reference_inference",
    "embedding_generation",
    "artifact_validation",
]


class IndexStageTrace(BaseModel):
    stage: str
    status: IndexStageStatus = "pending"
    progress: float = Field(default=0, ge=0, le=1)
    summary: str = ""
    artifact: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None


class SchemaIndexRunCreate(BaseModel):
    database_id: str = Field(min_length=1, max_length=120)


class SchemaIndexRunView(BaseModel):
    run_id: str
    index_id: str
    database_id: str
    status: IndexRunStatus = "created"
    created_at: datetime = Field(default_factory=schema_now)
    updated_at: datetime = Field(default_factory=schema_now)
    embedding_model: str
    stages: list[IndexStageTrace] = Field(default_factory=list)
    node_count: int = 0
    collection_count: int = 0
    value_count: int = 0
    dynamic_key_count: int = 0
    array_path_count: int = 0
    reference_edge_count: int = 0
    embedding_count: int = 0
    schema_hash: str | None = None
    artifact_dir: str | None = None
    failure: str | None = None


class ValueProfile(BaseModel):
    mode: Literal["range", "enum", "exact", "none"]
    distinct_count: int = 0
    min: int | float | str | None = None
    max: int | float | str | None = None


class KeyProfile(BaseModel):
    mode: Literal["range", "enum", "exact"]
    semantic_type: str
    distinct_count: int
    min: int | float | str | None = None
    max: int | float | str | None = None
    values: list[str | int | float] = Field(default_factory=list)


class SchemaIndexNode(BaseModel):
    id: str
    collection: str
    path: str
    parent_id: str | None
    types: list[str] = Field(default_factory=list)
    search_text: str
    context_text: str
    search_embedding_ref: str | None = None
    context_embedding_ref: str | None = None
    value_profile: ValueProfile | None = None
    key_profile: KeyProfile | None = None


class SchemaReferenceEdge(BaseModel):
    source_id: str
    target_id: str
    source_distinct_count: int
    matched_distinct_count: int
    overlap: float = Field(ge=0, le=1)
    inferred_by: Literal["declared", "strict_inference"]


class SchemaIndexSummary(BaseModel):
    index_id: str
    database_id: str
    schema_hash: str
    node_count: int
    collection_count: int
    value_count: int
    dynamic_key_count: int
    array_path_count: int
    reference_edge_count: int
    embedding_count: int
    embedding_model: str
    created_at: datetime

