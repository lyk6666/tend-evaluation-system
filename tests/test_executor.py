from pathlib import Path
from types import SimpleNamespace

import pytest

from tend_eval.config import Settings
from tend_eval.executor import GeneratedMQLRejected, TendMethodExecutor
from tend_eval.store import RunStore


@pytest.mark.asyncio
async def test_gpt5_chat_adapter_removes_unsupported_fields() -> None:
    captured = {}

    class Completions:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return {"ok": True}

    completions = Completions()
    runtime = SimpleNamespace(
        ctx=SimpleNamespace(
            llm=SimpleNamespace(
                _client=SimpleNamespace(
                    chat=SimpleNamespace(completions=completions)
                )
            )
        )
    )
    TendMethodExecutor._install_openai_chat_compat(runtime)
    await completions.create(
        model="gpt-5.6-luna",
        messages=[],
        temperature=0.0,
        max_tokens=2048,
        reasoning_effort="medium",
    )
    assert "temperature" not in captured
    assert "max_tokens" not in captured
    assert captured["max_completion_tokens"] == 2048
    assert captured["reasoning_effort"] == "medium"


def test_generated_mql_acceptance_gate_requires_nonempty_executable_query(tmp_path: Path) -> None:
    class Cursor:
        closed = False

        def __next__(self):
            raise StopIteration

        def close(self):
            self.closed = True

    cursor = Cursor()

    class Collection:
        def aggregate(self, pipeline, **kwargs):
            assert pipeline == []
            assert kwargs["maxTimeMS"] == 30_000
            return cursor

    class Mongo:
        def raw_database(self, db_id):
            assert db_id == "db-a"
            return {"collection": Collection()}

    settings = Settings(_env_file=None, TEND_EVAL_RUNTIME_DIR=tmp_path / "runtime")
    executor = TendMethodExecutor(settings, RunStore(settings.sqlite_path))
    runtime = SimpleNamespace(mongo=Mongo())

    executor._validate_generated_mql(
        runtime,
        "db-a",
        {"result_type": "solver_prediction", "MQL": "db.collection.aggregate([])"},
    )
    assert cursor.closed
    with pytest.raises(GeneratedMQLRejected, match="empty MQL") as empty:
        executor._validate_generated_mql(
            runtime, "db-a", {"result_type": "solver_prediction", "MQL": ""}
        )
    assert empty.value.consumes_generation_attempt

    with pytest.raises(GeneratedMQLRejected, match="did not produce") as unavailable:
        executor._validate_generated_mql(
            runtime,
            "db-a",
            {"result_type": "solver_failure", "error_code": "LLM_ERROR", "MQL": ""},
        )
    assert not unavailable.value.consumes_generation_attempt
