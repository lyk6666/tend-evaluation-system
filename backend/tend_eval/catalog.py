from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .config import Settings


@dataclass(frozen=True, slots=True)
class MethodSpec:
    id: str
    title: str
    family: str
    description: str
    supports_self_consistency: bool = False


METHODS: tuple[MethodSpec, ...] = (
    MethodSpec("direct_nlq_only", "NLQ-only floor", "baseline", "Question only; no schema, samples, database access, or repair.", True),
    MethodSpec("schema_direct", "Released-schema direct", "baseline", "One-shot generation using the sanitized released schema.", True),
    MethodSpec("direct", "Direct NL-to-MQL", "baseline", "One-shot generation using a small document sample.", True),
    MethodSpec("data_rich_direct", "Data-rich direct", "baseline", "One-shot generation using a larger document sample.", True),
    MethodSpec("sql_pivot", "SQL pivot workflow", "baseline", "Draft a SQL sketch and translate it to MQL.", True),
    MethodSpec("plan_then_mql", "Plan then MQL", "baseline", "Create a compact plan and convert it to MQL.", True),
    MethodSpec("react_lite", "Pure ReAct-lite", "baseline", "One reasoning turn followed by final MQL.", True),
    MethodSpec("static_self_debug", "Static self-debug", "baseline", "Draft, receive static feedback, and repair once.", True),
    MethodSpec("react_informed", "Fair ReAct (informed)", "baseline", "Bounded read-only ReAct loop with collection names.", False),
    MethodSpec("sag_v3", "SAG v3", "sag", "Full Schema-as-Data Grounding solver with result consistency.", False),
)

TRACKS = (
    {"id": "canonical", "field": "NLQ", "title": "Canonical", "description": "Official benchmark questions."},
    {"id": "robustness", "field": "NLQ_colloquial", "title": "Colloquial robustness", "description": "Paraphrased user-style questions."},
)


def _upstream_commit(source_dir: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(source_dir), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


def _official_baseline_ids(source_dir: Path) -> list[str] | None:
    src_dir = source_dir / "src"
    if not src_dir.is_dir():
        return None
    src_text = str(src_dir)
    if src_text not in sys.path:
        sys.path.insert(0, src_text)
    try:
        from tend.baselines import BASELINE_IDS
    except Exception:
        return None
    return list(BASELINE_IDS)


def load_dataset_summary(dataset_path: Path) -> dict[str, Any]:
    if not dataset_path.is_file():
        return {
            "available": False,
            "path": str(dataset_path),
            "task_count": 0,
            "database_count": 0,
            "tasks_per_database": {},
        }
    try:
        records = json.loads(dataset_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return {
            "available": False,
            "path": str(dataset_path),
            "task_count": 0,
            "database_count": 0,
            "tasks_per_database": {},
            "error": str(error),
        }
    counts = Counter(str(item.get("db_id") or "unknown") for item in records)
    robustness_count = sum(bool(item.get("NLQ_colloquial")) for item in records)
    return {
        "available": True,
        "path": str(dataset_path),
        "task_count": len(records),
        "database_count": len(counts),
        "databases": sorted(counts),
        "tasks_per_database": dict(sorted(counts.items())),
        "robustness_task_count": robustness_count,
        "balanced_110": bool(counts) and set(counts.values()) == {110},
    }


def build_catalog(settings: Settings) -> dict[str, Any]:
    configured = [asdict(method) for method in METHODS]
    official_ids = _official_baseline_ids(settings.tend_source_dir)
    expected_ids = [method.id for method in METHODS if method.family == "baseline"]
    return {
        "methods": configured,
        "tracks": list(TRACKS),
        "dataset": load_dataset_summary(settings.dataset_path),
        "upstream": {
            "available": settings.tend_source_dir.is_dir(),
            "source_dir": str(settings.tend_source_dir),
            "commit": _upstream_commit(settings.tend_source_dir),
            "official_baseline_ids": official_ids,
            "catalog_matches_upstream": official_ids == expected_ids if official_ids else None,
        },
    }

