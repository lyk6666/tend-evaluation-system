from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any

from .catalog import METHODS
from .config import Settings
from .contracts import RunCreate, RunMode


_CONSISTENCY_ID = re.compile(r"^sc(?P<k>[2-9][0-9]*)_(?P<base>[a-z0-9_]+)$")


def validate_method_id(method_id: str) -> bool:
    methods = {method.id: method for method in METHODS}
    if method_id in methods:
        return True
    match = _CONSISTENCY_ID.fullmatch(method_id)
    if not match:
        return False
    base = methods.get(match.group("base"))
    return bool(base and base.family == "baseline" and base.supports_self_consistency)


@lru_cache(maxsize=4)
def _load_records(path_text: str) -> tuple[dict[str, Any], ...]:
    with open(path_text, encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, list):
        raise ValueError("TEND dataset must contain a JSON array")
    return tuple(payload)


def build_work_items(request: RunCreate, settings: Settings) -> list[dict[str, Any]]:
    unknown = [method_id for method_id in request.method_ids if not validate_method_id(method_id)]
    if unknown:
        raise ValueError(f"unknown or unsupported method ids: {', '.join(unknown)}")

    if request.mode == RunMode.CUSTOM_QUERY:
        question = str(request.question or "").strip()
        return [
            {
                "ordinal": ordinal,
                "method_id": method_id,
                "track": "custom",
                "db_id": str(request.database_id),
                "record_id": None,
                "question": question,
                "payload": {"execute": request.execute_custom_query},
            }
            for ordinal, method_id in enumerate(request.method_ids)
        ]

    if not settings.dataset_path.is_file():
        raise FileNotFoundError(f"TEND dataset not found: {settings.dataset_path}")
    records = _load_records(str(settings.dataset_path))
    available_databases = sorted({str(record.get("db_id")) for record in records})
    selected_databases = request.database_ids or available_databases
    unknown_databases = sorted(set(selected_databases) - set(available_databases))
    if unknown_databases:
        raise ValueError(f"unknown database ids: {', '.join(unknown_databases)}")

    selected_set = set(selected_databases)
    filtered_records = [record for record in records if str(record.get("db_id")) in selected_set]
    work_items: list[dict[str, Any]] = []
    ordinal = 0
    for method_id in request.method_ids:
        for track in request.tracks:
            question_field = "NLQ" if track == "canonical" else "NLQ_colloquial"
            for record in filtered_records:
                question = record.get(question_field)
                if not isinstance(question, str) or not question.strip():
                    raise ValueError(
                        f"record {record.get('record_id')} has no {question_field} question"
                    )
                work_items.append(
                    {
                        "ordinal": ordinal,
                        "method_id": method_id,
                        "track": track,
                        "db_id": str(record.get("db_id")),
                        "record_id": record.get("record_id"),
                        "question": question,
                        "payload": {"question_field": question_field},
                    }
                )
                ordinal += 1
    if not work_items:
        raise ValueError("run selection produced no work items")
    return work_items

