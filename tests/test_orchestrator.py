import asyncio
from pathlib import Path

import pytest

from tend_eval.contracts import RunCreate, RunMode, RunStatus
from tend_eval.executor import GeneratedMQLRejected
from tend_eval.orchestrator import RunOrchestrator
from tend_eval.store import RunStore


def _items(count: int):
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


@pytest.mark.asyncio
async def test_orchestrator_runs_with_configured_concurrency(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs.sqlite3")
    store.initialize()
    active = 0
    peak = 0

    async def executor(item):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.03)
        active -= 1
        return {"item": item.id}

    request = RunCreate(
        mode=RunMode.CUSTOM_QUERY,
        method_ids=["direct"],
        database_id="financial",
        question="test",
        concurrency=2,
    )
    run = store.create_run(
        request, _items(5), model="gpt-5.6-luna", reasoning_effort="medium", concurrency=2
    )
    orchestrator = RunOrchestrator(store, executor, poll_interval=0.01)
    await orchestrator.start()
    try:
        for _ in range(200):
            current = store.get_run(run.id)
            if current and current.status == RunStatus.COMPLETED:
                break
            await asyncio.sleep(0.01)
        current = store.get_run(run.id)
        assert current and current.status == RunStatus.COMPLETED
        assert current.succeeded_items == 5
        assert peak == 2
    finally:
        await orchestrator.stop()


@pytest.mark.asyncio
async def test_orchestrator_pause_resume_and_cancel_are_task_safe(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs.sqlite3")
    store.initialize()
    release_first = asyncio.Event()

    async def executor(item):
        if item.ordinal == 0:
            await release_first.wait()
        else:
            await asyncio.sleep(0.02)
        return {"item": item.id}

    request = RunCreate(
        mode=RunMode.CUSTOM_QUERY,
        method_ids=["direct"],
        database_id="financial",
        question="test",
        concurrency=1,
    )
    run = store.create_run(
        request, _items(4), model="gpt-5.6-luna", reasoning_effort="medium", concurrency=1
    )
    orchestrator = RunOrchestrator(store, executor, poll_interval=0.01)
    await orchestrator.start()
    try:
        for _ in range(100):
            current = store.get_run(run.id)
            if current and current.running_items == 1:
                break
            await asyncio.sleep(0.01)
        paused = store.pause(run.id)
        assert paused and paused.status == RunStatus.PAUSING
        release_first.set()
        for _ in range(100):
            current = store.get_run(run.id)
            if current and current.status == RunStatus.PAUSED:
                break
            await asyncio.sleep(0.01)
        current = store.get_run(run.id)
        assert current and current.status == RunStatus.PAUSED
        assert current.succeeded_items == 1
        assert current.pending_items == 3

        resumed = store.resume(run.id)
        assert resumed and resumed.status == RunStatus.QUEUED
        for _ in range(100):
            current = store.get_run(run.id)
            if current and current.running_items:
                break
            await asyncio.sleep(0.01)
        cancelling = store.request_cancel(run.id)
        assert cancelling and cancelling.status == RunStatus.CANCELLING
        for _ in range(100):
            current = store.get_run(run.id)
            if current and current.status == RunStatus.CANCELLED:
                break
            await asyncio.sleep(0.01)
        current = store.get_run(run.id)
        assert current and current.status == RunStatus.CANCELLED
        assert current.succeeded_items + current.cancelled_items == 4
    finally:
        await orchestrator.stop()


@pytest.mark.asyncio
async def test_orchestrator_regenerates_rejected_attempt_until_accepted(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs.sqlite3")
    store.initialize()

    async def executor(item):
        if item.attempt == 1:
            raise GeneratedMQLRejected("empty MQL", consumes_generation_attempt=True)
        return {"item": item.id, "MQL": "db.collection.aggregate([])"}

    request = RunCreate(
        mode=RunMode.CUSTOM_QUERY,
        method_ids=["direct"],
        database_id="financial",
        question="test",
        concurrency=1,
    )
    run = store.create_run(
        request, _items(1), model="gpt-5.6-luna", reasoning_effort="medium", concurrency=1
    )
    orchestrator = RunOrchestrator(
        store,
        executor,
        poll_interval=0.005,
        retry_initial_delay=0.01,
        retry_max_delay=0.01,
    )
    await orchestrator.start()
    try:
        for _ in range(400):
            current = store.get_run(run.id)
            if current and current.status == RunStatus.COMPLETED:
                break
            await asyncio.sleep(0.01)
        current = store.get_run(run.id)
        assert current and current.status == RunStatus.COMPLETED
        assert current.succeeded_items == 1
        item = store.list_work_items(run.id)[0]
        assert item.attempt == 2
        assert item.generation_attempt == 1
        assert item.status == "succeeded"
    finally:
        await orchestrator.stop()


@pytest.mark.asyncio
async def test_orchestrator_blocks_after_the_generation_attempt_budget(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs.sqlite3")
    store.initialize()

    async def executor(item):
        raise GeneratedMQLRejected("empty MQL", consumes_generation_attempt=True)

    request = RunCreate(
        mode=RunMode.CUSTOM_QUERY,
        method_ids=["direct"],
        database_id="financial",
        question="test",
        concurrency=1,
    )
    run = store.create_run(
        request, _items(1), model="gpt-5.6-luna", reasoning_effort="medium", concurrency=1
    )
    orchestrator = RunOrchestrator(
        store,
        executor,
        poll_interval=0.005,
        retry_initial_delay=0.01,
        retry_max_delay=0.01,
        max_generation_attempts=2,
    )
    await orchestrator.start()
    try:
        for _ in range(400):
            current = store.get_run(run.id)
            if current and current.status == RunStatus.FAILED:
                break
            await asyncio.sleep(0.01)
        current = store.get_run(run.id)
        assert current and current.status == RunStatus.FAILED
        assert current.failed_items == 1
        item = store.list_work_items(run.id)[0]
        assert item.attempt == 2
        assert item.status == "failed"
        assert item.generation_attempt == 2
        assert item.error and "blocked after 2 API-backed responses" in item.error
    finally:
        await orchestrator.stop()


@pytest.mark.asyncio
async def test_orchestrator_keeps_retrying_provider_failures_without_using_response_budget(
    tmp_path: Path,
) -> None:
    store = RunStore(tmp_path / "runs.sqlite3")
    store.initialize()

    async def executor(item):
        if item.attempt <= 3:
            raise RuntimeError("provider connection unavailable")
        return {"item": item.id, "MQL": "db.collection.aggregate([])"}

    request = RunCreate(
        mode=RunMode.CUSTOM_QUERY,
        method_ids=["direct"],
        database_id="financial",
        question="test",
        concurrency=1,
    )
    run = store.create_run(
        request, _items(1), model="gpt-5.6-luna", reasoning_effort="medium", concurrency=1
    )
    orchestrator = RunOrchestrator(
        store,
        executor,
        poll_interval=0.005,
        retry_initial_delay=0.01,
        retry_max_delay=0.01,
        max_generation_attempts=2,
    )
    await orchestrator.start()
    try:
        for _ in range(500):
            current = store.get_run(run.id)
            if current and current.status == RunStatus.COMPLETED:
                break
            await asyncio.sleep(0.01)
        current = store.get_run(run.id)
        assert current and current.status == RunStatus.COMPLETED
        item = store.list_work_items(run.id)[0]
        assert item.attempt == 4
        assert item.generation_attempt == 0
    finally:
        await orchestrator.stop()
