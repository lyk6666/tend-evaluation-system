from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


def mql_now() -> datetime:
    return datetime.now(UTC)


MQL_STAGE_NAMES = [
    "input_preparation",
    "candidate_generation",
    "deterministic_validation",
    "repair",
    "execution_selection",
    "tend_evaluation",
]


class MQLGenerationConfig(BaseModel):
    combination_ranks: list[int] = Field(default_factory=list, max_length=10)
    max_repair_attempts: int = Field(default=1, ge=0, le=3)
    preview_limit: int = Field(default=100, ge=1, le=500)


class MQLGenerationRunCreate(BaseModel):
    pruning_run_id: str
    config: MQLGenerationConfig = Field(default_factory=MQLGenerationConfig)


class MQLStageTrace(BaseModel):
    stage: str
    status: Literal["pending", "running", "completed", "failed"] = "pending"
    progress: float = Field(default=0, ge=0, le=1)
    summary: str = ""
    artifact: Any = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: float | None = None
    error: str | None = None


class MQLDraftResponse(BaseModel):
    collection: str = Field(min_length=1, max_length=200)
    pipeline: list[dict[str, Any]]
    rationale: str = Field(default="", max_length=4_000)

    @model_validator(mode="after")
    def non_empty_pipeline(self) -> "MQLDraftResponse":
        if not self.pipeline:
            raise ValueError("pipeline must contain at least one aggregation stage")
        return self


class MQLValidationIssue(BaseModel):
    severity: Literal["error", "warning", "info"]
    code: str
    message: str
    stage_index: int | None = None
    path: str | None = None


class MQLRepairAttempt(BaseModel):
    attempt: int
    input_issues: list[MQLValidationIssue] = Field(default_factory=list)
    collection: str | None = None
    pipeline: list[dict[str, Any]] = Field(default_factory=list)
    rationale: str = ""
    validation_issues: list[MQLValidationIssue] = Field(default_factory=list)
    accepted: bool = False


class MQLExecutionPreview(BaseModel):
    ok: bool
    collection: str
    rows: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    limit: int
    possibly_truncated: bool = False
    latency_ms: float | None = None
    error: str | None = None


class MQLCandidate(BaseModel):
    combination_rank: int
    schema_score: float
    status: Literal[
        "generation_failed", "generated", "invalid", "valid", "execution_failed",
        "executed", "selected"
    ]
    collection: str | None = None
    pipeline: list[dict[str, Any]] = Field(default_factory=list)
    mql: str | None = None
    rationale: str = ""
    used_paths: list[str] = Field(default_factory=list)
    validation_issues: list[MQLValidationIssue] = Field(default_factory=list)
    repairs: list[MQLRepairAttempt] = Field(default_factory=list)
    execution: MQLExecutionPreview | None = None
    failure: str | None = None


class TENDMetricResult(BaseModel):
    available: bool = False
    reason: str = ""
    record_id: int | str | None = None
    track: Literal["canonical", "robustness"] | None = None
    EXC: float | None = None
    EXF1: float | None = None
    outcome: str | None = None
    claim_axes: dict[str, str] = Field(default_factory=dict)
    gold_row_count: int | None = None
    predicted_row_count: int | None = None


class MQLGenerationRunView(BaseModel):
    run_id: str
    pruning_run_id: str
    bundle_run_id: str
    database_id: str
    question: str
    model_id: str
    status: Literal["created", "running", "completed", "failed"] = "created"
    created_at: datetime = Field(default_factory=mql_now)
    updated_at: datetime = Field(default_factory=mql_now)
    config: MQLGenerationConfig
    stages: list[MQLStageTrace] = Field(default_factory=list)
    candidates: list[MQLCandidate] = Field(default_factory=list)
    selected_combination_rank: int | None = None
    final_collection: str | None = None
    final_pipeline: list[dict[str, Any]] = Field(default_factory=list)
    final_mql: str | None = None
    final_result: MQLExecutionPreview | None = None
    tend_metrics: TENDMetricResult = Field(default_factory=TENDMetricResult)
    failure: str | None = None
