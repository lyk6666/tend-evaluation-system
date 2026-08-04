from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .mql_contracts import MQLGenerationRunView


class MQLGenerationRunStore:
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
                CREATE TABLE IF NOT EXISTS mql_generation_runs (
                    run_id TEXT PRIMARY KEY,
                    pruning_run_id TEXT NOT NULL,
                    bundle_run_id TEXT NOT NULL,
                    database_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_mql_generation_runs_updated
                    ON mql_generation_runs(updated_at DESC);
                """
            )

    def recover_interrupted(self) -> None:
        for run in self.list(500):
            if run.status == "running":
                run.status = "failed"
                run.failure = "MQL generation was interrupted by a backend restart; start a new run."
                for stage in run.stages:
                    if stage.status == "running":
                        stage.status = "failed"
                        stage.error = run.failure
                self.save(run)

    def save(self, run: MQLGenerationRunView) -> None:
        run.updated_at = datetime.now(UTC)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO mql_generation_runs (
                    run_id, pruning_run_id, bundle_run_id, database_id,
                    status, created_at, updated_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    status = excluded.status,
                    updated_at = excluded.updated_at,
                    payload_json = excluded.payload_json
                """,
                (
                    run.run_id,
                    run.pruning_run_id,
                    run.bundle_run_id,
                    run.database_id,
                    run.status,
                    run.created_at.isoformat(),
                    run.updated_at.isoformat(),
                    run.model_dump_json(),
                ),
            )
            connection.execute("COMMIT")

    def get(self, run_id: str) -> MQLGenerationRunView | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM mql_generation_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        return MQLGenerationRunView.model_validate_json(row["payload_json"]) if row else None

    def list(self, limit: int = 50) -> list[MQLGenerationRunView]:
        safe_limit = max(1, min(limit, 500))
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM mql_generation_runs ORDER BY updated_at DESC LIMIT ?",
                (safe_limit,),
            ).fetchall()
        return [MQLGenerationRunView.model_validate_json(row["payload_json"]) for row in rows]
