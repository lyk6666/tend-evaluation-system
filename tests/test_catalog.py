from pathlib import Path

from tend_eval.catalog import METHODS, load_dataset_summary


def test_method_catalog_contains_official_surface_without_ablations() -> None:
    ids = {method.id for method in METHODS}
    assert len(ids) == 10
    assert "sag_v3" in ids
    assert "react_informed" in ids
    assert not {"sag_card1", "sag_gate", "sag_v2"} & ids


def test_dataset_summary_counts_balanced_release(tmp_path: Path) -> None:
    path = tmp_path / "TEND.json"
    path.write_text(
        '[{"db_id":"a","NLQ_colloquial":"x"},{"db_id":"b","NLQ_colloquial":"y"}]',
        encoding="utf-8",
    )
    summary = load_dataset_summary(path)
    assert summary["available"] is True
    assert summary["task_count"] == 2
    assert summary["database_count"] == 2
    assert summary["robustness_task_count"] == 2
    assert summary["balanced_110"] is False

