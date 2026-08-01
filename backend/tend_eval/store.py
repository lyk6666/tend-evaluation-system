from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .contracts import EventView, RunCreate, RunMode, RunStatus, RunView, WorkItemView, WorkStatus


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class RunStore:
    """SQLite-backed run queue with task-boundary checkpoints and an append-only event log."""

    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
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
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    mode TEXT NOT NULL,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    method_ids_json TEXT NOT NULL,
                    tracks_json TEXT NOT NULL,
                    database_ids_json TEXT NOT NULL,
                    model TEXT NOT NULL,
                    reasoning_effort TEXT NOT NULL,
                    concurrency INTEGER NOT NULL,
                    execute_custom_query INTEGER NOT NULL DEFAULT 1,
                    total_items INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    error TEXT
                );
                CREATE TABLE IF NOT EXISTS work_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL,
                    method_id TEXT NOT NULL,
                    track TEXT NOT NULL,
                    db_id TEXT NOT NULL,
                    record_id TEXT,
                    question TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 0,
                    worker_id TEXT,
                    result_json TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    UNIQUE(run_id, ordinal)
                );
                CREATE INDEX IF NOT EXISTS idx_work_run_status
                    ON work_items(run_id, status, ordinal);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_events_run_id ON events(run_id, id);
                """
            )

    def recover_incomplete(self) -> None:
        """Return interrupted work to a safe task boundary after process restart."""
        now = utc_now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE work_items SET status = ?, worker_id = NULL, started_at = NULL "
                "WHERE status = ?",
                (WorkStatus.PENDING, WorkStatus.RUNNING),
            )
            connection.execute(
                "UPDATE runs SET status = ?, updated_at = ? WHERE status = ?",
                (RunStatus.QUEUED, now, RunStatus.RUNNING),
            )
            connection.execute(
                "UPDATE runs SET status = ?, updated_at = ? WHERE status = ?",
                (RunStatus.PAUSED, now, RunStatus.PAUSING),
            )
            cancelling = connection.execute(
                "SELECT id FROM runs WHERE status = ?", (RunStatus.CANCELLING,)
            ).fetchall()
            for row in cancelling:
                self._cancel_remaining(connection, str(row["id"]), now)
            connection.commit()

    def create_run(
        self,
        request: RunCreate,
        work_items: list[dict[str, Any]],
        *,
        model: str,
        reasoning_effort: str,
        concurrency: int,
    ) -> RunView:
        run_id = uuid.uuid4().hex
        now = utc_now()
        database_ids = list(dict.fromkeys(str(item["db_id"]) for item in work_items))
        name = request.name or (
            f"Benchmark · {len(request.method_ids)} methods"
            if request.mode == RunMode.BENCHMARK
            else f"Custom query · {request.database_id}"
        )
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO runs (
                    id, mode, name, status, method_ids_json, tracks_json,
                    database_ids_json, model, reasoning_effort, concurrency,
                    execute_custom_query, total_items, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    request.mode,
                    name,
                    RunStatus.QUEUED,
                    json.dumps(request.method_ids),
                    json.dumps(request.tracks if request.mode == RunMode.BENCHMARK else ["custom"]),
                    json.dumps(database_ids),
                    model,
                    reasoning_effort,
                    concurrency,
                    int(request.execute_custom_query),
                    len(work_items),
                    now,
                    now,
                ),
            )
            connection.executemany(
                """
                INSERT INTO work_items (
                    run_id, ordinal, method_id, track, db_id, record_id,
                    question, payload_json, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        run_id,
                        item["ordinal"],
                        item["method_id"],
                        item["track"],
                        item["db_id"],
                        None if item.get("record_id") is None else str(item["record_id"]),
                        item["question"],
                        json.dumps(item.get("payload") or {}, ensure_ascii=False),
                        WorkStatus.PENDING,
                        now,
                    )
                    for item in work_items
                ],
            )
            self._add_event(connection, run_id, "run_created", {"total_items": len(work_items)}, now)
            connection.commit()
        run = self.get_run(run_id)
        if run is None:
            raise RuntimeError("run creation failed")
        return run

    def list_runs(self, limit: int = 50) -> list[RunView]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [self._run_view(connection, row) for row in rows]

    def get_run(self, run_id: str) -> RunView | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            return self._run_view(connection, row) if row else None

    def actionable_run_ids(self) -> list[str]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id FROM runs WHERE status IN (?, ?, ?) ORDER BY created_at",
                (RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.CANCELLING),
            ).fetchall()
            return [str(row["id"]) for row in rows]

    def mark_running(self, run_id: str) -> None:
        now = utc_now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute(
                "UPDATE runs SET status = ?, started_at = COALESCE(started_at, ?), updated_at = ? "
                "WHERE id = ? AND status = ?",
                (RunStatus.RUNNING, now, now, run_id, RunStatus.QUEUED),
            ).rowcount
            if changed:
                self._add_event(connection, run_id, "run_started", {}, now)
            connection.commit()

    def claim_next(self, run_id: str, worker_id: str) -> WorkItemView | None:
        now = utc_now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            run = connection.execute("SELECT status FROM runs WHERE id = ?", (run_id,)).fetchone()
            if not run or run["status"] != RunStatus.RUNNING:
                connection.rollback()
                return None
            row = connection.execute(
                "SELECT * FROM work_items WHERE run_id = ? AND status = ? "
                "ORDER BY ordinal LIMIT 1",
                (run_id, WorkStatus.PENDING),
            ).fetchone()
            if row is None:
                connection.rollback()
                return None
            connection.execute(
                "UPDATE work_items SET status = ?, attempt = attempt + 1, worker_id = ?, "
                "started_at = ?, finished_at = NULL, error = NULL WHERE id = ?",
                (WorkStatus.RUNNING, worker_id, now, row["id"]),
            )
            self._add_event(
                connection,
                run_id,
                "work_started",
                {"work_item_id": row["id"], "method_id": row["method_id"], "db_id": row["db_id"]},
                now,
            )
            connection.commit()
            claimed = connection.execute(
                "SELECT * FROM work_items WHERE id = ?", (row["id"],)
            ).fetchone()
            return self._work_view(claimed)

    def finish_work(
        self,
        work_item_id: int,
        *,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        cancelled: bool = False,
    ) -> None:
        now = utc_now()
        status = WorkStatus.CANCELLED if cancelled else WorkStatus.FAILED if error else WorkStatus.SUCCEEDED
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT run_id, method_id, db_id FROM work_items WHERE id = ?", (work_item_id,)
            ).fetchone()
            if row is None:
                connection.rollback()
                return
            connection.execute(
                "UPDATE work_items SET status = ?, result_json = ?, error = ?, "
                "finished_at = ?, worker_id = NULL WHERE id = ?",
                (
                    status,
                    json.dumps(result, ensure_ascii=False) if result is not None else None,
                    error,
                    now,
                    work_item_id,
                ),
            )
            self._add_event(
                connection,
                str(row["run_id"]),
                "work_finished",
                {
                    "work_item_id": work_item_id,
                    "method_id": row["method_id"],
                    "db_id": row["db_id"],
                    "status": status,
                    "error": error,
                },
                now,
            )
            connection.execute(
                "UPDATE runs SET updated_at = ? WHERE id = ?", (now, row["run_id"])
            )
            connection.commit()

    def requeue_work(self, work_item_id: int) -> None:
        """Return an interrupted item to pending without counting a failed attempt."""
        now = utc_now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT run_id FROM work_items WHERE id = ? AND status = ?",
                (work_item_id, WorkStatus.RUNNING),
            ).fetchone()
            if row is not None:
                connection.execute(
                    "UPDATE work_items SET status = ?, worker_id = NULL, started_at = NULL "
                    "WHERE id = ?",
                    (WorkStatus.PENDING, work_item_id),
                )
                self._add_event(
                    connection,
                    str(row["run_id"]),
                    "work_requeued",
                    {"work_item_id": work_item_id},
                    now,
                )
            connection.commit()

    def pause(self, run_id: str) -> RunView | None:
        now = utc_now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT status FROM runs WHERE id = ?", (run_id,)).fetchone()
            if row and row["status"] in {RunStatus.QUEUED, RunStatus.RUNNING}:
                running_count = connection.execute(
                    "SELECT COUNT(*) FROM work_items WHERE run_id = ? AND status = ?",
                    (run_id, WorkStatus.RUNNING),
                ).fetchone()[0]
                status = RunStatus.PAUSING if running_count else RunStatus.PAUSED
                connection.execute(
                    "UPDATE runs SET status = ?, updated_at = ? WHERE id = ?",
                    (status, now, run_id),
                )
                self._add_event(connection, run_id, "pause_requested", {"status": status}, now)
            connection.commit()
        return self.get_run(run_id)

    def mark_paused(self, run_id: str) -> None:
        now = utc_now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute(
                "UPDATE runs SET status = ?, updated_at = ? WHERE id = ? AND status = ?",
                (RunStatus.PAUSED, now, run_id, RunStatus.PAUSING),
            ).rowcount
            if changed:
                self._add_event(connection, run_id, "run_paused", {}, now)
            connection.commit()

    def resume(self, run_id: str) -> RunView | None:
        now = utc_now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute(
                "UPDATE runs SET status = ?, updated_at = ?, finished_at = NULL "
                "WHERE id = ? AND status = ?",
                (RunStatus.QUEUED, now, run_id, RunStatus.PAUSED),
            ).rowcount
            if changed:
                self._add_event(connection, run_id, "run_resumed", {}, now)
            connection.commit()
        return self.get_run(run_id)

    def request_cancel(self, run_id: str) -> RunView | None:
        now = utc_now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT status FROM runs WHERE id = ?", (run_id,)).fetchone()
            terminal = {RunStatus.CANCELLED, RunStatus.COMPLETED, RunStatus.FAILED}
            if row and row["status"] not in terminal:
                connection.execute(
                    "UPDATE runs SET status = ?, updated_at = ? WHERE id = ?",
                    (RunStatus.CANCELLING, now, run_id),
                )
                self._add_event(connection, run_id, "cancel_requested", {}, now)
            connection.commit()
        return self.get_run(run_id)

    def cancel_remaining(self, run_id: str) -> None:
        now = utc_now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._cancel_remaining(connection, run_id, now)
            connection.commit()

    def _cancel_remaining(self, connection: sqlite3.Connection, run_id: str, now: str) -> None:
        connection.execute(
            "UPDATE work_items SET status = ?, finished_at = ?, worker_id = NULL "
            "WHERE run_id = ? AND status IN (?, ?)",
            (WorkStatus.CANCELLED, now, run_id, WorkStatus.PENDING, WorkStatus.RUNNING),
        )
        connection.execute(
            "UPDATE runs SET status = ?, updated_at = ?, finished_at = ? WHERE id = ?",
            (RunStatus.CANCELLED, now, now, run_id),
        )
        self._add_event(connection, run_id, "run_cancelled", {}, now)

    def finalize_if_complete(self, run_id: str) -> RunView | None:
        now = utc_now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            counts = self._status_counts(connection, run_id)
            unfinished = counts[WorkStatus.PENDING] + counts[WorkStatus.RUNNING]
            run = connection.execute("SELECT status FROM runs WHERE id = ?", (run_id,)).fetchone()
            if run and unfinished == 0 and run["status"] == RunStatus.RUNNING:
                final_status = RunStatus.FAILED if counts[WorkStatus.FAILED] else RunStatus.COMPLETED
                connection.execute(
                    "UPDATE runs SET status = ?, updated_at = ?, finished_at = ? WHERE id = ?",
                    (final_status, now, now, run_id),
                )
                self._add_event(
                    connection,
                    run_id,
                    "run_finished",
                    {"status": final_status, "counts": {key: counts[key] for key in counts}},
                    now,
                )
            connection.commit()
        return self.get_run(run_id)

    def list_work_items(
        self,
        run_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
        status: WorkStatus | None = None,
    ) -> list[WorkItemView]:
        with self.connect() as connection:
            if status is None:
                rows = connection.execute(
                    "SELECT * FROM work_items WHERE run_id = ? ORDER BY ordinal LIMIT ? OFFSET ?",
                    (run_id, limit, offset),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM work_items WHERE run_id = ? AND status = ? "
                    "ORDER BY ordinal LIMIT ? OFFSET ?",
                    (run_id, status, limit, offset),
                ).fetchall()
            return [self._work_view(row) for row in rows]

    def list_events(self, run_id: str, *, after: int = 0, limit: int = 500) -> list[EventView]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM events WHERE run_id = ? AND id > ? ORDER BY id LIMIT ?",
                (run_id, after, limit),
            ).fetchall()
            return [
                EventView(
                    id=row["id"],
                    run_id=row["run_id"],
                    type=row["type"],
                    payload=json.loads(row["payload_json"]),
                    created_at=row["created_at"],
                )
                for row in rows
            ]

    @staticmethod
    def _add_event(
        connection: sqlite3.Connection,
        run_id: str,
        event_type: str,
        payload: dict[str, Any],
        created_at: str,
    ) -> None:
        connection.execute(
            "INSERT INTO events (run_id, type, payload_json, created_at) VALUES (?, ?, ?, ?)",
            (run_id, event_type, json.dumps(payload, ensure_ascii=False), created_at),
        )

    @staticmethod
    def _status_counts(connection: sqlite3.Connection, run_id: str) -> dict[str, int]:
        counts = {status.value: 0 for status in WorkStatus}
        rows = connection.execute(
            "SELECT status, COUNT(*) AS count FROM work_items WHERE run_id = ? GROUP BY status",
            (run_id,),
        ).fetchall()
        for row in rows:
            counts[str(row["status"])] = int(row["count"])
        return counts

    def _run_view(self, connection: sqlite3.Connection, row: sqlite3.Row) -> RunView:
        counts = self._status_counts(connection, str(row["id"]))
        total = int(row["total_items"])
        finished = counts[WorkStatus.SUCCEEDED] + counts[WorkStatus.FAILED] + counts[WorkStatus.CANCELLED]
        return RunView(
            id=row["id"],
            mode=row["mode"],
            name=row["name"],
            status=row["status"],
            method_ids=json.loads(row["method_ids_json"]),
            tracks=json.loads(row["tracks_json"]),
            database_ids=json.loads(row["database_ids_json"]),
            model=row["model"],
            reasoning_effort=row["reasoning_effort"],
            concurrency=row["concurrency"],
            execute_custom_query=bool(row["execute_custom_query"]),
            total_items=total,
            pending_items=counts[WorkStatus.PENDING],
            running_items=counts[WorkStatus.RUNNING],
            succeeded_items=counts[WorkStatus.SUCCEEDED],
            failed_items=counts[WorkStatus.FAILED],
            cancelled_items=counts[WorkStatus.CANCELLED],
            progress=finished / total if total else 0.0,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            error=row["error"],
        )

    @staticmethod
    def _work_view(row: sqlite3.Row) -> WorkItemView:
        record_text = row["record_id"]
        record_id: int | str | None = record_text
        if isinstance(record_text, str) and record_text.isdigit():
            record_id = int(record_text)
        return WorkItemView(
            id=row["id"],
            run_id=row["run_id"],
            ordinal=row["ordinal"],
            method_id=row["method_id"],
            track=row["track"],
            db_id=row["db_id"],
            record_id=record_id,
            question=row["question"],
            payload=json.loads(row["payload_json"]),
            status=row["status"],
            attempt=row["attempt"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            error=row["error"],
            result=json.loads(row["result_json"]) if row["result_json"] else None,
        )
