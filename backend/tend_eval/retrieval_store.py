from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .retrieval_contracts import SchemaPruningRunView


class SchemaPruningRunStore:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_pruning_runs (
                    run_id TEXT PRIMARY KEY,
                    bundle_run_id TEXT NOT NULL,
                    index_id TEXT NOT NULL,
                    database_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_schema_pruning_runs_updated
                    ON schema_pruning_runs(updated_at DESC);
                """
            )

    def recover_interrupted(self) -> None:
        for run in self.list(500):
            if run.status == "running":
                run.status = "failed"
                run.failure = "Retrieval was interrupted by a backend restart; start a new run."
                for stage in run.stages:
                    if stage.status == "running":
                        stage.status = "failed"
                        stage.error = run.failure
                self.save(run)

    def save(self, run: SchemaPruningRunView) -> None:
        run.updated_at = datetime.now(UTC)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO schema_pruning_runs (
                    run_id, bundle_run_id, index_id, database_id,
                    status, created_at, updated_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    status = excluded.status,
                    updated_at = excluded.updated_at,
                    payload_json = excluded.payload_json
                """,
                (
                    run.run_id,
                    run.bundle_run_id,
                    run.index_id,
                    run.database_id,
                    run.status,
                    run.created_at.isoformat(),
                    run.updated_at.isoformat(),
                    run.model_dump_json(),
                ),
            )
            connection.execute("COMMIT")

    def get(self, run_id: str) -> SchemaPruningRunView | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM schema_pruning_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        return SchemaPruningRunView.model_validate_json(row["payload_json"]) if row else None

    def list(self, limit: int = 50) -> list[SchemaPruningRunView]:
        safe_limit = max(1, min(limit, 500))
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM schema_pruning_runs ORDER BY updated_at DESC LIMIT ?",
                (safe_limit,),
            ).fetchall()
        return [SchemaPruningRunView.model_validate_json(row["payload_json"]) for row in rows]

