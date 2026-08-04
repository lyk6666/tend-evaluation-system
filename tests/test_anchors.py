from pathlib import Path

from fastapi.testclient import TestClient

from tend_eval.anchors import AnchorExtractionPipeline, StructuredAnchorLLM
from tend_eval.config import Settings
from tend_eval.contracts import AnchorKind, AnchorRunCreate, AnchorRunMode, AnchorRunStatus
from tend_eval.main import create_app
from tend_eval.store import AnchorRunStore


QUESTION = (
    "For the 2021 season, find the highest-scoring driver for each constructor "
    "that won at least one race. Return the constructor name, driver's full name, "
    "total points earned for that constructor, and number of podium finishes. "
    "Return all tied drivers and order the results by total points descending."
)


def settings_for(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        TEND_SOURCE_DIR=tmp_path / "upstream",
        TEND_RELEASE_DIR=tmp_path / "release",
        TEND_EVAL_RUNTIME_DIR=tmp_path / "runtime",
    )


def test_retrieval_pipeline_persists_all_six_stages(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    store = AnchorRunStore(settings.sqlite_path)
    store.initialize()
    pipeline = AnchorExtractionPipeline(settings, store)
    created = pipeline.create_run(
        AnchorRunCreate(question=QUESTION, mode=AnchorRunMode.DETERMINISTIC)
    )

    pipeline.run(created.run_id)
    finished = store.get(created.run_id)

    assert finished is not None
    assert finished.status == AnchorRunStatus.COMPLETED
    assert len(finished.stages) == 6
    assert all(stage.status == "completed" for stage in finished.stages)
    assert finished.normalized_question is not None
    assert {cue.canonical for cue in finished.normalized_question.cues} >= {
        "ARGMAX",
        "PARTITION",
        "GTE",
        "COUNT",
        "KEEP_ALL_TIES",
        "DESC",
    }
    assert [stage.stage for stage in finished.stages] == [
        "normalization",
        "target_extraction",
        "support_inference",
        "restriction_binding",
        "retrieval_graph",
        "retrieval_bundle",
    ]
    assert finished.retrieval_bundle is not None
    assert any(
        item.kind == AnchorKind.ENTITY and item.canonical == "constructor"
        for item in finished.retrieval_bundle.targets
    )
    assert "for" not in {item.canonical for item in finished.retrieval_bundle.targets}
    assert {item.kind for item in finished.retrieval_bundle.targets} <= {
        AnchorKind.ENTITY,
        AnchorKind.FIELD,
        AnchorKind.DERIVED_CONCEPT,
    }
    assert any(
        item.canonical == "finishing position" and item.role == "supporting"
        for item in finished.retrieval_bundle.targets
    )
    assert finished.restriction_binding is not None
    assert any(
        item.kind == "temporal" and item.normalized_value == 2021
        for item in finished.restriction_binding.restrictions
    )
    by_id = {item.id: item for item in finished.retrieval_bundle.targets}
    ranking = next(
        item
        for item in finished.restriction_binding.restrictions
        if item.operator == "ARGMAX"
    )
    assert {by_id[item].canonical for item in ranking.anchor_ids} == {"points"}
    assert {item.kind for item in finished.restriction_binding.deferred_plan_cues} >= {
        "sorting",
        "tie_policy",
    }
    assert finished.retrieval_bundle.model_dump().keys() == {
        "question",
        "targets",
        "relations",
        "value_constraints",
    }
    assert all("mention" not in item.model_dump() for item in finished.retrieval_bundle.targets)
    assert finished.retrieval_bundle.value_constraints[0].value == 2021


def test_anchor_api_is_available_without_dataset_or_mongodb(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        created = client.post(
            "/api/new-methods/anchor-runs",
            json={"question": QUESTION, "mode": "deterministic"},
        )
        assert created.status_code == 202
        run_id = created.json()["run_id"]
        fetched = client.get(f"/api/new-methods/anchor-runs/{run_id}")
        assert fetched.status_code == 200
        assert fetched.json()["status"] == "completed"
        assert set(fetched.json()["retrieval_bundle"]) == {
            "question",
            "targets",
            "relations",
            "value_constraints",
        }
        assert all("mention" not in item for item in fetched.json()["retrieval_bundle"]["targets"])
        deleted = client.delete(f"/api/new-methods/anchor-runs/{run_id}")
        assert deleted.status_code == 204


def test_auto_mode_falls_back_when_semantic_provider_is_unavailable(
    tmp_path: Path, monkeypatch
) -> None:
    settings = Settings(
        _env_file=None,
        OPENAI_API_KEY="test-key",
        TEND_SOURCE_DIR=tmp_path / "upstream",
        TEND_RELEASE_DIR=tmp_path / "release",
        TEND_EVAL_RUNTIME_DIR=tmp_path / "runtime",
    )
    store = AnchorRunStore(settings.sqlite_path)
    store.initialize()
    pipeline = AnchorExtractionPipeline(settings, store)

    def fail_parse(**_kwargs):
        raise ConnectionError("offline")

    monkeypatch.setattr(pipeline.llm, "parse", fail_parse)
    created = pipeline.create_run(AnchorRunCreate(question=QUESTION, mode=AnchorRunMode.AUTO))
    pipeline.run(created.run_id)
    finished = store.get(created.run_id)

    assert finished is not None
    assert finished.status == AnchorRunStatus.COMPLETED
    assert finished.support_inference is not None
    assert any("auto mode" in note for note in finished.support_inference.notes)


def test_llm_json_schema_is_closed_for_strict_output() -> None:
    from tend_eval.contracts import SupportInference

    schema = StructuredAnchorLLM._strict_schema(SupportInference.model_json_schema())
    assert schema["additionalProperties"] is False
    assert schema["required"] == list(schema["properties"])
