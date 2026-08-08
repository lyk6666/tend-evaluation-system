"""One-record, no-provider smoke test for the official evaluator and existing MongoDB."""
from __future__ import annotations

import asyncio
import json
import os
import uuid

from tend_eval.config import Settings
from tend_eval.contracts import RunCreate
from tend_eval.evaluation import load_results
from tend_eval.executor import TendMethodExecutor
from tend_eval.store import RunStore


async def main() -> None:
    settings = Settings(
        _env_file=None,
        TEND_EVAL_LLM_STUB=True,
        TEND_EVAL_RUNTIME_DIR=f"data/runtime/evaluator-smoke-{uuid.uuid4().hex[:8]}",
    )
    records = json.loads(settings.dataset_path.read_text(encoding="utf-8"))
    record_id = int(os.getenv("TEND_EVAL_SMOKE_RECORD_ID", "362579"))
    record = next(
        (candidate for candidate in records if candidate.get("record_id") == record_id),
        None,
    )
    if record is None:
        raise RuntimeError(f"smoke record not found: {record_id}")
    store = RunStore(settings.sqlite_path)
    store.initialize()
    request = RunCreate(
        mode="benchmark",
        method_ids=["direct"],
        tracks=["canonical"],
        database_ids=[record["db_id"]],
        concurrency=1,
    )
    run = store.create_run(
        request,
        [
            {
                "ordinal": 0,
                "method_id": "direct",
                "track": "canonical",
                "db_id": record["db_id"],
                "record_id": record["record_id"],
                "question": record["NLQ"],
                "payload": {},
            }
        ],
        model=settings.model,
        reasoning_effort=settings.reasoning_effort,
        concurrency=1,
    )
    store.mark_running(run.id)
    item = store.claim_next(run.id, "smoke")
    if item is None:
        raise RuntimeError("failed to claim smoke work item")
    store.finish_work(
        item.id,
        result={
            "result_type": "baseline_prediction",
            "baseline_id": "direct",
            "db_id": record["db_id"],
            "record_id": record["record_id"],
            "MQL": record["MQL"],
        },
    )
    store.finalize_if_complete(run.id)
    executor = TendMethodExecutor(settings, store)
    try:
        await executor.finalize_run(run.id)
        results = load_results(settings, store, run.id)
        report = (results or {}).get("reports", {}).get("canonical", {})
        score = report.get("systems", {}).get("direct", {}).get("scores", {}).get("EXC")
        if score != 1.0:
            raise RuntimeError(f"expected EXC=1.0, got {score!r}")
        print(json.dumps({"run_id": run.id, "status": "ok", "EXC": score}))
    finally:
        await executor.close_run(run.id)


if __name__ == "__main__":
    asyncio.run(main())
