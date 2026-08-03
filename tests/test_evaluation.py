import json
from pathlib import Path

from tend_eval.config import Settings
from tend_eval.contracts import WorkItemView
from tend_eval.evaluation import (
    OfficialEvaluationService,
    _EvaluationMongoExecutor,
    _system_slice_aggregates,
)
from tend_eval.store import RunStore


def make_item(**overrides) -> WorkItemView:
    values = {
        "id": 1,
        "run_id": "run-1",
        "ordinal": 0,
        "method_id": "direct",
        "track": "canonical",
        "db_id": "db-a",
        "record_id": 7,
        "question": "question",
        "payload": {},
        "status": "succeeded",
        "attempt": 1,
        "result": {"result_type": "baseline_prediction", "MQL": "db.c.aggregate([])"},
    }
    values.update(overrides)
    return WorkItemView(**values)


def test_prediction_normalizes_system_identity_and_failures() -> None:
    baseline = OfficialEvaluationService._prediction_for(make_item())
    sag = OfficialEvaluationService._prediction_for(
        make_item(method_id="sag_v3", result={"result_type": "solver_prediction", "MQL": "x"})
    )
    failed = OfficialEvaluationService._prediction_for(
        make_item(status="failed", result=None, error="provider unavailable")
    )

    assert baseline["baseline_id"] == "direct"
    assert "solver_variant" not in baseline
    assert sag["solver_variant"] == "sag_v3"
    assert "baseline_id" not in sag
    assert failed["result_type"] == "baseline_failure"
    assert failed["error_code"] == "work_item_failed"
    assert "MQL" not in failed


def test_only_nonempty_successful_mql_is_an_accepted_prediction() -> None:
    accepted = make_item()
    empty = make_item(result={"result_type": "baseline_prediction", "MQL": ""})
    failed = make_item(result={"result_type": "baseline_failure", "MQL": "db.c.aggregate([])"})

    assert OfficialEvaluationService._is_accepted_prediction(accepted)
    assert not OfficialEvaluationService._is_accepted_prediction(empty)
    assert not OfficialEvaluationService._is_accepted_prediction(failed)


def test_evaluation_dataset_is_scoped_and_links_official_release_files(tmp_path: Path) -> None:
    release = tmp_path / "release"
    (release / "data").mkdir(parents=True)
    (release / "mongodb_data").mkdir()
    (release / "schema" / "mongodb_schema").mkdir(parents=True)
    (release / "data" / "TEND.json").write_text(
        json.dumps(
            [
                {"db_id": "db-a", "record_id": 7, "MQL": "db.c.aggregate([])"},
                {"db_id": "db-b", "record_id": 8, "MQL": "db.c.aggregate([])"},
            ]
        ),
        encoding="utf-8",
    )
    witness = '{"collection": [{"value": 1}]}'
    schema = '{"collection": {"value": "int"}}'
    (release / "mongodb_data" / "db-a.json").write_text(witness, encoding="utf-8")
    (release / "schema" / "mongodb_schema" / "db-a.json").write_text(
        schema, encoding="utf-8"
    )
    settings = Settings(
        _env_file=None,
        TEND_RELEASE_DIR=release,
        TEND_SOURCE_DIR=tmp_path / "source",
        TEND_EVAL_RUNTIME_DIR=tmp_path / "runtime",
    )
    service = OfficialEvaluationService(settings, RunStore(settings.sqlite_path))
    dataset = service._write_evaluation_dataset(
        settings.runtime_dir / "runs" / "run-1" / "evaluation",
        [make_item()],
        "run-1",
    )

    selected = json.loads((dataset / "data" / "TEND.json").read_text(encoding="utf-8"))
    assert [(row["db_id"], row["record_id"]) for row in selected] == [("db-a", 7)]
    assert (dataset / "mongodb_data" / "db-a.json").read_text(encoding="utf-8") == witness
    assert (
        dataset / "schema" / "mongodb_schema" / "db-a.json"
    ).read_text(encoding="utf-8") == schema
    manifest = json.loads((dataset / "evaluation_selection.json").read_text(encoding="utf-8"))
    assert manifest["record_count"] == 1


def test_evaluation_executor_uses_configured_timeout(monkeypatch) -> None:
    calls = []

    class Collection:
        def aggregate(self, pipeline, **kwargs):
            calls.append((pipeline, kwargs))
            return [{"answer": 1}]

    class Database:
        def __getitem__(self, name):
            assert name == "collection"
            return Collection()

    class Delegate:
        def available(self):
            return True

        def load_witness(self, db_id, collections):
            return None

        def raw_database(self, db_id):
            assert db_id == "db-a"
            return Database()

    monkeypatch.setattr("tend.execution.mongo._normalize_doc", lambda document: document)
    gold_mql = 'db.collection.aggregate([{"$limit": 1}])'
    executor = _EvaluationMongoExecutor(
        Delegate(),
        120_000,
        frozenset({("db-a", gold_mql)}),
    )
    result = executor.norm_exec("db-a", gold_mql)
    executor.norm_exec("db-a", "db.collection.aggregate([])")

    assert result == [{"answer": 1}]
    assert calls == [
        ([{"$limit": 1}], {"maxTimeMS": 120_000}),
        ([], {"maxTimeMS": 30_000}),
    ]


def test_system_slice_aggregates_are_per_method(tmp_path: Path) -> None:
    path = tmp_path / "records.jsonl"
    rows = [
        {
            "system_id": "direct",
            "metrics": {"EXC": 1, "EXF1": 0.8},
            "slice_keys": {"domain": "finance"},
        },
        {
            "system_id": "direct",
            "metrics": {"EXC": 0, "EXF1": 0.2},
            "slice_keys": {"domain": "finance"},
        },
        {
            "system_id": "sag_v3",
            "metrics": {"EXC": 1, "EXF1": 1},
            "slice_keys": {"domain": "finance"},
        },
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    result = _system_slice_aggregates(path)
    assert result["direct"]["domain"]["finance"]["scores"] == {"EXC": 0.5, "EXF1": 0.5}
    assert result["sag_v3"]["domain"]["finance"]["record_count"] == 1
