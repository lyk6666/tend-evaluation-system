import json
import sqlite3
import struct
from pathlib import Path

from tend_eval.anchors import AnchorExtractionPipeline
from tend_eval.config import Settings
from tend_eval.contracts import (
    AnchorRunCreate,
    AnchorRunMode,
    RetrievalBundle,
    RetrievalBundleRelation,
    RetrievalBundleTarget,
    RetrievalValueConstraint,
)
from tend_eval.retrieval_contracts import SchemaPruningRunCreate
from tend_eval.retrieval_store import SchemaPruningRunStore
from tend_eval.schema_contracts import KeyProfile, SchemaIndexRunView, ValueProfile
from tend_eval.schema_indexing import EmbeddingClient, SchemaIndexManager, SchemaTreeBuilder
from tend_eval.schema_retrieval import SchemaGraph, SchemaPruningManager
from tend_eval.schema_store import SchemaIndexRunStore
from tend_eval.store import AnchorRunStore

from test_schema_indexing import FakeCollection, SCHEMA, settings_for


def build_test_index(settings: Settings, tmp_path: Path) -> tuple[SchemaIndexRunStore, str]:
    store = SchemaIndexRunStore(settings.sqlite_path)
    store.initialize()
    indexer = SchemaIndexManager(settings, store)
    nodes = SchemaTreeBuilder().build(SCHEMA)
    artifact = tmp_path / "index"
    artifact.mkdir()
    values_path = artifact / "values.sqlite3"
    indexer._initialize_values(values_path)
    profile_path = tmp_path / "profile.json"
    indexer._profile_collection(FakeCollection(), nodes, values_path, profile_path)
    profiles = json.loads(profile_path.read_text(encoding="utf-8"))
    for node in nodes:
        if profiles.get(node.id, {}).get("value_profile"):
            node.value_profile = ValueProfile.model_validate(profiles[node.id]["value_profile"])
        if profiles.get(node.id, {}).get("key_profile"):
            node.key_profile = KeyProfile.model_validate(profiles[node.id]["key_profile"])
    with (artifact / "nodes.profiled.jsonl").open("w", encoding="utf-8") as stream:
        for node in nodes:
            stream.write(node.model_dump_json() + "\n")
    (artifact / "reference_edges.json").write_text("[]", encoding="utf-8")
    embeddings_path = artifact / "embeddings.sqlite3"
    indexer._initialize_embeddings(embeddings_path)
    embedding_client = EmbeddingClient(settings)
    rows = []
    for node in nodes:
        for suffix, text in (("search", node.search_text), ("context", node.context_text)):
            vector = embedding_client.embed([text or node.collection])[0]
            rows.append(
                (
                    f"{node.id}#{suffix}",
                    settings.embedding_model,
                    "test",
                    len(vector),
                    struct.pack(f"<{len(vector)}f", *vector),
                )
            )
    with sqlite3.connect(embeddings_path) as connection:
        connection.executemany(
            "INSERT INTO embeddings VALUES (?, ?, ?, ?, ?)",
            rows,
        )
    (artifact / "READY").write_text("validated\n", encoding="utf-8")
    run = SchemaIndexRunView(
        run_id="index-demo",
        index_id="index-demo",
        database_id="demo",
        status="completed",
        embedding_model=settings.embedding_model,
        artifact_dir=str(artifact),
        node_count=len(nodes),
        embedding_count=len(rows),
    )
    store.save(run)
    return store, run.index_id


def build_bundle(settings: Settings) -> tuple[AnchorRunStore, str]:
    store = AnchorRunStore(settings.sqlite_path)
    store.initialize()
    pipeline = AnchorExtractionPipeline(settings, store)
    run = pipeline.create_run(
        AnchorRunCreate(question="Find points in the 2021 results", mode=AnchorRunMode.DETERMINISTIC)
    )
    pipeline.run(run.run_id)
    run = store.get(run.run_id)
    assert run is not None
    run.retrieval_bundle = RetrievalBundle(
        question=run.question,
        targets=[
            RetrievalBundleTarget(
                id="results",
                kind="entity",
                role="primary",
                canonical="results",
            ),
            RetrievalBundleTarget(
                id="points",
                kind="field",
                role="primary",
                canonical="points",
                parent_hints=["results"],
                expected_types=["number"],
            ),
            RetrievalBundleTarget(
                id="season",
                kind="field",
                role="supporting",
                canonical="season year",
                expected_types=["integer"],
            ),
        ],
        relations=[
            RetrievalBundleRelation(source="points", target="results", type="belongs_to"),
            RetrievalBundleRelation(source="season", target="results", type="requires_connection"),
        ],
        value_constraints=[
            RetrievalValueConstraint(kind="value", target_ids=["points"], operator="=", value=25),
            RetrievalValueConstraint(kind="temporal", target_ids=["season"], operator="=", value=2021),
        ],
    )
    store.save(run)
    return store, run.run_id


def test_pruning_persists_intermediate_results_and_exact_value_evidence(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    anchor_store, bundle_id = build_bundle(settings)
    index_store, index_id = build_test_index(settings, tmp_path)
    pruning_store = SchemaPruningRunStore(settings.sqlite_path)
    pruning_store.initialize()
    manager = SchemaPruningManager(settings, pruning_store, anchor_store, index_store)
    run = manager.create(SchemaPruningRunCreate(bundle_run_id=bundle_id, index_id=index_id))
    run.status = "running"
    pruning_store.save(run)
    manager._run(run.run_id)

    finished = pruning_store.get(run.run_id)
    assert finished is not None
    assert finished.status == "completed"
    assert all(stage.status == "completed" and stage.artifact is not None for stage in finished.stages)
    assert finished.search_requests[0].query_text
    points = next(
        candidate
        for candidate in finished.candidates_by_target["points"]
        if candidate.path_id == "races:results[].points"
    )
    assert points.signals.exact_contains is False
    assert points.signals.value == 0
    season = finished.candidates_by_target["season"][0]
    assert season.path_id == "races:seasons.*"
    assert season.signals.exact_contains is True
    assert season.binding == {"dynamic_key_values": [2021]}
    assert finished.relationship_evaluations
    assert finished.combinations
    assert finished.pruned_schemas
    assert any(node.role == "connector" for node in finished.pruned_schemas[0].nodes)


def test_relationship_formulas_are_structural_and_collection_aware() -> None:
    nodes = SchemaTreeBuilder().build(SCHEMA)
    graph = SchemaGraph(nodes, [])
    belongs = graph.evaluate(
        0, "belongs_to", "points", "results", "races:results[].points", "races:results"
    )
    assert belongs.resolved is True
    assert belongs.score == 1
    scoped = graph.evaluate(
        0, "scoped_with", "points", "driver", "races:results[].points", "races:results[].driver_id"
    )
    assert scoped.resolved is True
    assert scoped.score == 1
