from pathlib import Path

import tend_eval.mql_generation as mql_module
from tend_eval.anchors import AnchorExtractionPipeline
from tend_eval.contracts import AnchorRunCreate, AnchorRunMode
from tend_eval.mql_contracts import MQLDraftResponse, MQLGenerationRunCreate
from tend_eval.mql_generation import MQLGenerationManager, MQLValidator
from tend_eval.mql_store import MQLGenerationRunStore
from tend_eval.retrieval_contracts import (
    PrunedNode,
    PrunedSchema,
    PruningConfig,
    SchemaPruningRunView,
)
from tend_eval.retrieval_store import SchemaPruningRunStore
from tend_eval.store import AnchorRunStore

from test_schema_indexing import settings_for


class FakeCursor:
    def __init__(self, rows):
        self.rows = iter(rows)

    def __iter__(self):
        return self

    def __next__(self):
        return next(self.rows)

    def close(self):
        return None


class FakeCollection:
    def aggregate(self, pipeline, **_kwargs):
        return FakeCursor([{"season": 2021, "pipeline_stages": len(pipeline)}])


class FakeDatabase:
    def __getitem__(self, _collection):
        return FakeCollection()


class FakeAdmin:
    def command(self, _name):
        return {"ok": 1}


class FakeMongoClient:
    admin = FakeAdmin()

    def __getitem__(self, _database):
        return FakeDatabase()

    def close(self):
        return None


class FakeGenerator:
    def generate(self, **_kwargs):
        return MQLDraftResponse(
            collection="races",
            pipeline=[
                {"$match": {"season": 2021}},
                {"$project": {"_id": 0, "season": 1}},
            ],
            rationale="Filter and return the grounded season path.",
        )


def test_mql_generation_persists_all_stages_and_execution(tmp_path: Path, monkeypatch) -> None:
    settings = settings_for(tmp_path)
    settings.dataset_path.parent.mkdir(parents=True, exist_ok=True)
    settings.dataset_path.write_text("[]", encoding="utf-8")
    anchor_store = AnchorRunStore(settings.sqlite_path)
    anchor_store.initialize()
    pipeline = AnchorExtractionPipeline(settings, anchor_store)
    anchor = pipeline.create_run(
        AnchorRunCreate(
            question="Find race results for season 2021",
            mode=AnchorRunMode.DETERMINISTIC,
        )
    )
    pipeline.run(anchor.run_id)

    pruning_store = SchemaPruningRunStore(settings.sqlite_path)
    pruning_store.initialize()
    pruning = SchemaPruningRunView(
        run_id="pruning-1",
        bundle_run_id=anchor.run_id,
        index_id="index-1",
        database_id="demo",
        status="completed",
        config=PruningConfig(),
        pruned_schemas=[
            PrunedSchema(
                combination_rank=1,
                score=0.91,
                nodes=[
                    PrunedNode(
                        id="races:root",
                        collection="races",
                        path="",
                        parent_id=None,
                        types=["object"],
                        role="ancestor",
                    ),
                    PrunedNode(
                        id="races:season",
                        collection="races",
                        path="season",
                        parent_id="races:root",
                        types=["integer"],
                        role="target",
                        target_ids=["s1"],
                    ),
                ],
            )
        ],
    )
    pruning_store.save(pruning)
    store = MQLGenerationRunStore(settings.sqlite_path)
    store.initialize()
    manager = MQLGenerationManager(
        settings,
        store,
        pruning_store,
        anchor_store,
        generator=FakeGenerator(),
    )
    monkeypatch.setattr(mql_module, "MongoClient", lambda *_args, **_kwargs: FakeMongoClient())
    run = manager.create(MQLGenerationRunCreate(pruning_run_id=pruning.run_id))
    run.status = "running"
    store.save(run)
    manager._run(run.run_id)

    finished = store.get(run.run_id)
    assert finished is not None
    assert finished.status == "completed"
    assert all(stage.status == "completed" and stage.artifact is not None for stage in finished.stages)
    assert finished.final_collection == "races"
    assert finished.final_pipeline[0] == {"$match": {"season": 2021}}
    assert finished.final_result is not None and finished.final_result.ok
    assert finished.candidates[0].status == "selected"
    assert finished.tend_metrics.available is False
    assert "No exact official TEND task" in finished.tend_metrics.reason


def test_validator_rejects_unsafe_and_ungrounded_paths() -> None:
    schema = PrunedSchema(
        combination_rank=1,
        score=1,
        nodes=[
            PrunedNode(
                id="races:root",
                collection="races",
                path="",
                parent_id=None,
                types=["object"],
                role="ancestor",
            ),
            PrunedNode(
                id="races:season",
                collection="races",
                path="season",
                parent_id="races:root",
                types=["integer"],
                role="target",
                target_ids=["s1"],
            ),
        ],
    )
    validator = MQLValidator(FakeMongoClient(), "demo", max_time_ms=1_000)
    issues, _ = validator.validate(
        "races",
        [{"$match": {"unknown": 1}}, {"$out": "stolen"}],
        schema,
        probe=False,
    )
    codes = {item.code for item in issues}
    assert "PATH_OUTSIDE_PRUNED_SCHEMA" in codes
    assert "BANNED_OPERATOR" in codes
