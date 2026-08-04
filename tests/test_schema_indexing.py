import json
import sqlite3
from pathlib import Path

from tend_eval.config import Settings
from tend_eval.schema_indexing import SchemaIndexManager, SchemaTreeBuilder
from tend_eval.schema_store import SchemaIndexRunStore


SCHEMA = {
    "database_name": "demo",
    "collections": {
        "races": {
            "schema": {
                "seasons": {
                    "type": "object",
                    "properties": {
                        "2020": {"type": "object", "properties": {"wins": {"type": "integer"}}},
                        "2021": {"type": "object", "properties": {"wins": {"type": "integer"}}},
                        "2023": {"type": "object", "properties": {"wins": {"type": "integer"}}},
                    },
                },
                "results": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "driver_id": {"type": "integer"},
                            "points": {"type": "number"},
                        },
                    },
                },
            }
        }
    },
}


class FakeCollection:
    def find(self, *_args, **_kwargs):
        return [
            {
                "seasons": {"2020": {"wins": 2}, "2021": {"wins": 5}, "2023": {"wins": 7}},
                "results": [
                    {"driver_id": 1, "points": 0},
                    {"driver_id": 2, "points": 10},
                    {"driver_id": 3, "points": 20},
                    {"driver_id": 4, "points": 30},
                    {"driver_id": 5, "points": 50},
                ],
            }
        ]


def settings_for(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        TEND_EVAL_LLM_STUB=True,
        TEND_EVAL_RUNTIME_DIR=tmp_path / "runtime",
        TEND_SOURCE_DIR=tmp_path / "source",
        TEND_RELEASE_DIR=tmp_path / "release",
    )


def test_tree_builder_uses_array_markers_and_key_semantic_type() -> None:
    nodes = SchemaTreeBuilder().build(SCHEMA)
    by_path = {item.path: item for item in nodes}

    assert "results[].points" in by_path
    assert by_path["results[].points"].search_text == "points"
    assert by_path["results[].points"].context_text == "races results"
    assert by_path["seasons.*"].search_text == "year"
    assert by_path["seasons.*"].key_profile is not None
    assert by_path["seasons.*"].key_profile.values == [2020, 2021, 2023]


def test_value_profile_keeps_exact_membership_beside_range(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    store = SchemaIndexRunStore(settings.sqlite_path)
    store.initialize()
    manager = SchemaIndexManager(settings, store)
    nodes = SchemaTreeBuilder().build(SCHEMA)
    values_path = tmp_path / "values.sqlite3"
    manager._initialize_values(values_path)
    profile_path = tmp_path / "races.json"

    manager._profile_collection(FakeCollection(), nodes, values_path, profile_path)
    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    points = payload["races:results[].points"]["value_profile"]

    assert points == {"mode": "range", "distinct_count": 5, "min": 0.0, "max": 50.0}
    with sqlite3.connect(values_path) as connection:
        exact_25 = connection.execute(
            "SELECT 1 FROM path_values WHERE path_id = ? AND normalized_value = ?",
            ("races:results[].points", "25"),
        ).fetchone()
        exact_30 = connection.execute(
            "SELECT 1 FROM path_values WHERE path_id = ? AND normalized_value = ?",
            ("races:results[].points", "30"),
        ).fetchone()
    assert exact_25 is None
    assert exact_30 is not None

