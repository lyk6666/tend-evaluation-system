from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .schema_contracts import SchemaIndexRunView


class SchemaIndexRunStore:
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
                CREATE TABLE IF NOT EXISTS schema_index_runs (
                    run_id TEXT PRIMARY KEY,
                    index_id TEXT NOT NULL,
                    database_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_schema_index_runs_updated
                    ON schema_index_runs(updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_schema_index_runs_database
                    ON schema_index_runs(database_id, updated_at DESC);
                """
            )

    def recover_interrupted(self) -> None:
        for run in self.list(500):
            if run.status in {"running", "pausing"}:
                run.status = "paused"
                run.failure = "Build interrupted by backend restart; resume to continue."
                for stage in run.stages:
                    if stage.status == "running":
                        stage.status = "paused"
                        stage.summary = "Interrupted; resumable from the last verified checkpoint."
                self.save(run)

    def save(self, run: SchemaIndexRunView) -> None:
        run.updated_at = datetime.now(UTC)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO schema_index_runs (
                    run_id, index_id, database_id, status, created_at, updated_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    status = excluded.status,
                    updated_at = excluded.updated_at,
                    payload_json = excluded.payload_json
                """,
                (
                    run.run_id,
                    run.index_id,
                    run.database_id,
                    run.status,
                    run.created_at.isoformat(),
                    run.updated_at.isoformat(),
                    run.model_dump_json(),
                ),
            )
            connection.execute("COMMIT")

    def get(self, run_id: str) -> SchemaIndexRunView | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM schema_index_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        return SchemaIndexRunView.model_validate_json(row["payload_json"]) if row else None

    def list(self, limit: int = 50) -> list[SchemaIndexRunView]:
        safe_limit = max(1, min(limit, 500))
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM schema_index_runs ORDER BY updated_at DESC LIMIT ?",
                (safe_limit,),
            ).fetchall()
        return [SchemaIndexRunView.model_validate_json(row["payload_json"]) for row in rows]

    def delete(self, run_id: str) -> bool:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "DELETE FROM schema_index_runs WHERE run_id = ?", (run_id,)
            )
            connection.execute("COMMIT")
            return cursor.rowcount > 0

