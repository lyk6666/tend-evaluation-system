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


def test_deterministic_anchor_pipeline_persists_all_seven_stages(tmp_path: Path) -> None:
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
    assert len(finished.stages) == 7
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
    assert finished.anchor_bundle is not None
    assert any(
        item.kind == AnchorKind.ENTITY and item.canonical == "constructor"
        for item in finished.anchor_bundle.anchors
    )
    assert any(item.search_kind == "path" for item in finished.anchor_bundle.retrieval_specifications)
    assert finished.anchor_bundle.ready_for_retrieval


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
        assert fetched.json()["anchor_bundle"]["retrieval_specifications"]
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
    assert finished.semantic_extraction is not None
    assert any("auto mode" in note for note in finished.semantic_extraction.notes)


def test_llm_json_schema_is_closed_for_strict_output() -> None:
    from tend_eval.contracts import SemanticAnchorExtraction

    schema = StructuredAnchorLLM._strict_schema(SemanticAnchorExtraction.model_json_schema())
    assert schema["additionalProperties"] is False
    assert schema["required"] == list(schema["properties"])
