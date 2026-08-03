from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def anchor_now() -> datetime:
    return datetime.now(UTC)


class RunMode(StrEnum):
    BENCHMARK = "benchmark"
    CUSTOM_QUERY = "custom_query"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSING = "pausing"
    PAUSED = "paused"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"


class WorkStatus(StrEnum):
    PENDING = "pending"
    RETRYING = "retrying"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class EvaluationStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class AnchorRunMode(StrEnum):
    AUTO = "auto"
    LLM = "llm"
    DETERMINISTIC = "deterministic"


class AnchorRunStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class AnchorKind(StrEnum):
    ENTITY = "entity"
    FIELD = "field"
    DERIVED_CONCEPT = "derived_concept"


class AnchorSource(StrEnum):
    RULE = "rule"
    LLM = "llm"
    INFERRED = "inferred"
    MERGED = "merged"


class NormalizedClause(BaseModel):
    clause_id: str
    text: str
    start: int
    end: int


class NormalizedScalar(BaseModel):
    scalar_id: str
    surface: str
    normalized: str | int | float | bool | None
    scalar_type: Literal["integer", "number", "year", "boolean", "string", "date"]
    context: str = ""
    start: int
    end: int


class NormalizationCue(BaseModel):
    cue_id: str
    surface: str
    category: Literal[
        "comparison",
        "ranking",
        "aggregation",
        "grouping",
        "sorting",
        "tie_policy",
        "output",
        "negation",
        "quantifier",
    ]
    canonical: str
    scope_hint: str = ""
    start: int
    end: int
    source: Literal["phrase_rule", "morphology", "syntax"] = "phrase_rule"


class NormalizedQuestion(BaseModel):
    original_text: str
    normalized_text: str
    locale: str = "en"
    timezone: str = "Asia/Shanghai"
    reference_time: datetime = Field(default_factory=anchor_now)
    clauses: list[NormalizedClause] = Field(default_factory=list)
    scalars: list[NormalizedScalar] = Field(default_factory=list)
    cues: list[NormalizationCue] = Field(default_factory=list)
    semantic_spans: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class TypedSemanticAnchor(BaseModel):
    anchor_id: str
    kind: AnchorKind
    surface: str
    canonical: str
    description: str
    retrieval_role: Literal["primary", "supporting"]
    expected_bson_types: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    parent_hints: list[str] = Field(default_factory=list)
    output_requested: bool = False
    derivation_hints: list[str] = Field(default_factory=list)
    explicit: bool = True
    source: AnchorSource
    start: int | None = None
    end: int | None = None
    confidence: float = Field(default=0.8, ge=0, le=1)


class AnchorRelation(BaseModel):
    relation_id: str
    source_anchor_id: str
    target_anchor_id: str
    relation_type: Literal[
        "belongs_to",
        "requires_connection",
        "scoped_with",
        "supports",
        "derived_from",
    ]
    description: str = ""
    confidence: float = Field(default=0.8, ge=0, le=1)
    source: AnchorSource = AnchorSource.INFERRED


class DeferredPlanCue(BaseModel):
    cue_id: str
    surface: str
    kind: Literal["sorting", "tie_policy", "presentation"]
    canonical: str
    reason: str
    start: int | None = None
    end: int | None = None


class TargetExtraction(BaseModel):
    anchors: list[TypedSemanticAnchor] = Field(default_factory=list)
    relations: list[AnchorRelation] = Field(default_factory=list)
    unresolved_phrases: list[str] = Field(default_factory=list)
    deferred_plan_cues: list["DeferredPlanCue"] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class SupportInference(BaseModel):
    anchors: list[TypedSemanticAnchor] = Field(default_factory=list)
    relations: list[AnchorRelation] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class RetrievalRestriction(BaseModel):
    restriction_id: str
    kind: Literal[
        "temporal",
        "value",
        "comparison",
        "cardinality",
        "role_hint",
        "eligibility",
        "scope",
    ]
    surface: str
    canonical: str
    description: str
    anchor_ids: list[str] = Field(default_factory=list)
    operator: str | None = None
    normalized_value: str | int | float | bool | None = None
    retrieval_effect: Literal[
        "filter_value", "type_hint", "role_hint", "support_requirement", "relation_scope"
    ]
    source: AnchorSource = AnchorSource.RULE
    start: int | None = None
    end: int | None = None
    confidence: float = Field(default=0.8, ge=0, le=1)


class RestrictionBinding(BaseModel):
    restrictions: list[RetrievalRestriction] = Field(default_factory=list)
    deferred_plan_cues: list[DeferredPlanCue] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class AnchorGraph(BaseModel):
    nodes: list[TypedSemanticAnchor] = Field(default_factory=list)
    edges: list[AnchorRelation] = Field(default_factory=list)
    restrictions: list[RetrievalRestriction] = Field(default_factory=list)
    root_anchor_ids: list[str] = Field(default_factory=list)
    connected_components: list[list[str]] = Field(default_factory=list)
    validation_warnings: list[str] = Field(default_factory=list)


class RetrievalSpecification(BaseModel):
    specification_id: str
    anchor_ids: list[str] = Field(default_factory=list)
    restriction_ids: list[str] = Field(default_factory=list)
    search_kind: Literal[
        "entity_or_group", "field_path", "derived_support", "relationship"
    ]
    query_terms: list[str] = Field(default_factory=list)
    semantic_query: str
    expected_bson_types: list[str] = Field(default_factory=list)
    structural_constraints: list[str] = Field(default_factory=list)
    required: bool = True
    rationale: str = ""


class RetrievalSpecificationSet(BaseModel):
    specifications: list[RetrievalSpecification] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class AnchorBundle(BaseModel):
    question: str
    normalized_question: NormalizedQuestion
    anchors: list[TypedSemanticAnchor] = Field(default_factory=list)
    relations: list[AnchorRelation] = Field(default_factory=list)
    restrictions: list[RetrievalRestriction] = Field(default_factory=list)
    deferred_plan_cues: list[DeferredPlanCue] = Field(default_factory=list)
    retrieval_specifications: list[RetrievalSpecification] = Field(default_factory=list)
    coverage_score: float = Field(default=0, ge=0, le=1)
    ready_for_retrieval: bool = False
    validation_warnings: list[str] = Field(default_factory=list)


class AnchorStageTrace(BaseModel):
    stage: str
    status: Literal["pending", "running", "completed", "failed", "skipped"]
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: float | None = None
    summary: str = ""
    artifact: Any = None
    error: str | None = None


class AnchorRunCreate(BaseModel):
    question: str = Field(min_length=3, max_length=20_000)
    mode: AnchorRunMode = AnchorRunMode.AUTO
    locale: str = Field(default="en", min_length=2, max_length=20)
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=100)


class AnchorRunView(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    run_id: str
    question: str
    mode: AnchorRunMode
    locale: str
    timezone: str
    status: AnchorRunStatus
    created_at: datetime = Field(default_factory=anchor_now)
    updated_at: datetime = Field(default_factory=anchor_now)
    model_id: str
    stages: list[AnchorStageTrace] = Field(default_factory=list)
    normalized_question: NormalizedQuestion | None = None
    target_extraction: TargetExtraction | None = None
    support_inference: SupportInference | None = None
    restriction_binding: RestrictionBinding | None = None
    retrieval_graph: AnchorGraph | None = None
    retrieval_specifications: RetrievalSpecificationSet | None = None
    retrieval_bundle: AnchorBundle | None = None
    failure: str | None = None


ANCHOR_STAGE_NAMES = [
    "normalization",
    "target_extraction",
    "support_inference",
    "restriction_binding",
    "retrieval_graph",
    "retrieval_specifications",
    "retrieval_bundle",
]


class RunCreate(BaseModel):
    mode: RunMode = RunMode.BENCHMARK
    name: str | None = Field(default=None, max_length=120)
    method_ids: list[str] = Field(min_length=1)
    tracks: list[Literal["canonical", "robustness"]] = Field(
        default_factory=lambda: ["canonical"]
    )
    database_ids: list[str] | None = None
    database_id: str | None = None
    question: str | None = Field(default=None, min_length=1, max_length=20_000)
    execute_custom_query: bool = True
    concurrency: int | None = Field(default=None, ge=1, le=128)
    model: str | None = Field(default=None, min_length=1, max_length=200)
    reasoning_effort: Literal["none", "low", "medium", "high", "xhigh", "max"] | None = None

    @model_validator(mode="after")
    def validate_mode_fields(self) -> "RunCreate":
        self.method_ids = list(dict.fromkeys(self.method_ids))
        self.tracks = list(dict.fromkeys(self.tracks))
        if self.mode == RunMode.BENCHMARK and not self.tracks:
            raise ValueError("benchmark runs require at least one evaluation track")
        if self.mode == RunMode.CUSTOM_QUERY:
            if not self.database_id:
                raise ValueError("custom-query runs require database_id")
            if not self.question or not self.question.strip():
                raise ValueError("custom-query runs require question")
        return self


class WorkItemView(BaseModel):
    id: int
    run_id: str
    ordinal: int
    method_id: str
    track: str
    db_id: str
    record_id: int | str | None
    question: str
    payload: dict[str, Any]
    status: WorkStatus
    attempt: int
    generation_attempt: int = 0
    retry_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    result: dict[str, Any] | None = None


class RunView(BaseModel):
    id: str
    mode: RunMode
    name: str
    status: RunStatus
    method_ids: list[str]
    tracks: list[str]
    database_ids: list[str]
    model: str
    reasoning_effort: str
    concurrency: int
    execute_custom_query: bool
    total_items: int
    pending_items: int
    retrying_items: int
    running_items: int
    succeeded_items: int
    failed_items: int
    cancelled_items: int
    progress: float
    created_at: str
    updated_at: str
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None


class EventView(BaseModel):
    id: int
    run_id: str
    type: str
    payload: dict[str, Any]
    created_at: str


class EvaluationView(BaseModel):
    run_id: str
    status: EvaluationStatus
    tracks: list[str]
    artifacts: dict[str, dict[str, str]] = Field(default_factory=dict)
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
