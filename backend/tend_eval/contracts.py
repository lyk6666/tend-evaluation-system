from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


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
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


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
