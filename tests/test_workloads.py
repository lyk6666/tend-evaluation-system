import json
from pathlib import Path

import pytest

from tend_eval.config import Settings
from tend_eval.contracts import RunCreate
from tend_eval.workloads import build_work_items, validate_method_id


def test_benchmark_expansion_is_method_track_record_product(tmp_path: Path) -> None:
    release_dir = tmp_path / "release"
    dataset_dir = release_dir / "data"
    dataset_dir.mkdir(parents=True)
    (dataset_dir / "TEND.json").write_text(
        json.dumps(
            [
                {"record_id": 1, "db_id": "a", "NLQ": "A?", "NLQ_colloquial": "Hey A?"},
                {"record_id": 2, "db_id": "b", "NLQ": "B?", "NLQ_colloquial": "Hey B?"},
            ]
        ),
        encoding="utf-8",
    )
    settings = Settings(
        _env_file=None,
        TEND_SOURCE_DIR=tmp_path / "upstream",
        TEND_RELEASE_DIR=release_dir,
        TEND_EVAL_RUNTIME_DIR=tmp_path / "runtime",
    )
    request = RunCreate(
        method_ids=["direct", "sag_v3"], tracks=["canonical", "robustness"]
    )
    items = build_work_items(request, settings)
    assert len(items) == 2 * 2 * 2
    assert {item["track"] for item in items} == {"canonical", "robustness"}


def test_method_validation_supports_official_dynamic_consistency_only() -> None:
    assert validate_method_id("sc3_direct")
    assert not validate_method_id("sc3_react_informed")
    assert not validate_method_id("sag_v2")

