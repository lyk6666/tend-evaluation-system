from pathlib import Path

from tend_eval.contracts import RunCreate, RunMode, RunStatus, WorkStatus
from tend_eval.store import RunStore


def _request() -> RunCreate:
    return RunCreate(
        mode=RunMode.CUSTOM_QUERY,
        method_ids=["direct"],
        database_id="financial",
        question="Which district has the highest salary?",
    )


def _items(count: int = 2):
    return [
        {
            "ordinal": index,
            "method_id": "direct",
            "track": "custom",
            "db_id": "financial",
            "record_id": None,
            "question": f"question {index}",
            "payload": {},
        }
        for index in range(count)
    ]


def test_store_claim_checkpoint_and_complete(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs.sqlite3")
    store.initialize()
    run = store.create_run(
        _request(), _items(), model="gpt-5.6-luna", reasoning_effort="medium", concurrency=1
    )
    store.mark_running(run.id)
    item = store.claim_next(run.id, "worker-test")
    assert item is not None
    assert item.status == WorkStatus.RUNNING
    store.finish_work(item.id, result={"ok": True})
    second = store.claim_next(run.id, "worker-test")
    assert second is not None
    store.finish_work(second.id, result={"ok": True})
    final = store.finalize_if_complete(run.id)
    assert final is not None
    assert final.status == RunStatus.COMPLETED
    assert final.succeeded_items == 2
    assert final.progress == 1.0


def test_pause_resume_cancel_and_recovery(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs.sqlite3")
    store.initialize()
    run = store.create_run(
        _request(), _items(3), model="gpt-5.6-luna", reasoning_effort="medium", concurrency=1
    )
    paused = store.pause(run.id)
    assert paused and paused.status == RunStatus.PAUSED
    resumed = store.resume(run.id)
    assert resumed and resumed.status == RunStatus.QUEUED
    store.mark_running(run.id)
    assert store.claim_next(run.id, "worker-test") is not None
    store.recover_incomplete()
    recovered = store.get_run(run.id)
    assert recovered and recovered.status == RunStatus.QUEUED
    assert recovered.running_items == 0
    cancelled = store.request_cancel(run.id)
    assert cancelled and cancelled.status == RunStatus.CANCELLING
    store.cancel_remaining(run.id)
    final = store.get_run(run.id)
    assert final and final.status == RunStatus.CANCELLED
    assert final.cancelled_items == 3


def test_benchmark_evaluation_lifecycle(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs.sqlite3")
    store.initialize()
    request = RunCreate(
        mode=RunMode.BENCHMARK,
        method_ids=["direct"],
        tracks=["canonical", "robustness"],
        database_ids=["financial"],
    )
    items = [
        {
            "ordinal": 0,
            "method_id": "direct",
            "track": "canonical",
            "db_id": "financial",
            "record_id": 1,
            "question": "question",
            "payload": {},
        }
    ]
    run = store.create_run(
        request, items, model="gpt-5.6-luna", reasoning_effort="medium", concurrency=1
    )
    evaluation = store.get_evaluation(run.id)
    assert evaluation and evaluation.status == "pending"
    assert evaluation.tracks == ["canonical", "robustness"]
    assert store.mark_evaluation_running(run.id).status == "running"
    finished = store.finish_evaluation(run.id, {"canonical": {"report_json": "report.json"}})
    assert finished and finished.status == "completed"
    assert finished.artifacts["canonical"]["report_json"] == "report.json"
