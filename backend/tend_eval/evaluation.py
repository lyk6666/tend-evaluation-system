from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from .config import Settings
from .contracts import EvaluationStatus, WorkItemView, WorkStatus
from .store import RunStore


EXPORT_KINDS = frozenset(
    {"report_json", "report_md", "per_record_csv", "per_record_jsonl"}
)


class OfficialEvaluationService:
    """Run the upstream TEND evaluator and expose its unmodified report artifacts."""

    def __init__(self, settings: Settings, store: RunStore):
        self.settings = settings
        self.store = store

    async def finalize_run(self, run_id: str, runtime: Any) -> None:
        state = self.store.get_evaluation(run_id)
        if state is None or state.status != EvaluationStatus.PENDING:
            return
        state = self.store.mark_evaluation_running(run_id)
        if state is None or state.status != EvaluationStatus.RUNNING:
            return
        try:
            artifacts = await asyncio.to_thread(self._evaluate, run_id, runtime, state.tracks)
        except asyncio.CancelledError:
            self.store.request_evaluation(run_id)
            raise
        except Exception as error:  # noqa: BLE001 - preserve failure for monitoring/retry
            self.store.fail_evaluation(run_id, f"{type(error).__name__}: {error}")
        else:
            self.store.finish_evaluation(run_id, artifacts)

    def _evaluate(
        self, run_id: str, runtime: Any, tracks: list[str]
    ) -> dict[str, dict[str, str]]:
        from tend.evaluation.metrics import evaluate_predictions

        run = self.store.get_run(run_id)
        if run is None:
            raise RuntimeError(f"run not found: {run_id}")
        items = self.store.list_work_items(run_id, limit=max(run.total_items, 1))
        benchmark_items = [item for item in items if item.record_id is not None]
        if not benchmark_items:
            raise RuntimeError("benchmark run contains no evaluable work items")

        run_root = self.settings.runtime_dir / "runs" / run_id / "evaluation"
        dataset_dir = self._write_evaluation_dataset(run_root, benchmark_items, run_id)
        artifacts: dict[str, dict[str, str]] = {}
        for track in tracks:
            track_items = [item for item in benchmark_items if item.track == track]
            if not track_items:
                continue
            track_root = run_root / track
            predictions_path = track_root / "predictions.jsonl"
            self._write_predictions(predictions_path, track_items)
            output = evaluate_predictions(
                dataset_dir=dataset_dir,
                predictions_path=predictions_path,
                out_dir=track_root / "report",
                experiment_kind="evaluation_system",
                run_id=f"{run_id}:{track}",
                logger=runtime.log,
                executor=runtime.mongo,
                max_workers=run.concurrency,
            )
            if output.status == "failed":
                message = str((output.report.get("diagnostics") or {}).get("message") or "")
                raise RuntimeError(f"official TEND evaluation failed for {track}: {message}")
            artifacts[track] = output.paths.as_dict()
        if not artifacts:
            raise RuntimeError("no evaluation tracks produced artifacts")
        return artifacts

    def _write_evaluation_dataset(
        self, run_root: Path, items: list[WorkItemView], run_id: str
    ) -> Path:
        source_path = self.settings.tend_release_dir / "data" / "TEND.json"
        records = json.loads(source_path.read_text(encoding="utf-8"))
        selected = {(item.db_id, item.record_id) for item in items}
        subset = [
            record
            for record in records
            if (str(record.get("db_id") or ""), record.get("record_id")) in selected
        ]
        if len(subset) != len(selected):
            found = {(str(record.get("db_id") or ""), record.get("record_id")) for record in subset}
            missing = sorted(selected - found, key=lambda value: (value[0], str(value[1])))
            raise RuntimeError(f"{len(missing)} selected benchmark records are missing: {missing[:5]}")

        dataset_dir = run_root / "release"
        data_dir = dataset_dir / "data"
        witness_dir = dataset_dir / "mongodb_data"
        data_dir.mkdir(parents=True, exist_ok=True)
        witness_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "TEND.json").write_text(
            json.dumps(subset, ensure_ascii=False), encoding="utf-8"
        )
        for db_id in sorted({db_id for db_id, _ in selected}):
            # The official Mongo executor is configured to reuse the user's existing
            # databases, so load_witness() is a no-op. Empty mappings satisfy the release
            # layout without parsing the multi-gigabyte raw export again.
            (witness_dir / f"{db_id}.json").write_text("{}", encoding="utf-8")
        (dataset_dir / "evaluation_selection.json").write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "database_ids": sorted({db_id for db_id, _ in selected}),
                    "record_count": len(subset),
                    "source_release": str(self.settings.tend_release_dir),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return dataset_dir

    @staticmethod
    def _write_predictions(path: Path, items: list[WorkItemView]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            for item in sorted(items, key=lambda value: value.ordinal):
                payload = OfficialEvaluationService._prediction_for(item)
                handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")

    @staticmethod
    def _prediction_for(item: WorkItemView) -> dict[str, Any]:
        payload = dict(item.result or {})
        payload.update(
            {
                "run_id": item.run_id,
                "work_item_id": item.id,
                "db_id": item.db_id,
                "record_id": item.record_id,
                "method_id": item.method_id,
                "nlq_track": item.track,
            }
        )
        for key in ("ablation_id", "baseline_id", "solver_variant", "system_id"):
            payload.pop(key, None)
        if item.method_id == "sag_v3":
            payload["solver_variant"] = item.method_id
            failure_type = "solver_failure"
        else:
            payload["baseline_id"] = item.method_id
            failure_type = "baseline_failure"
        if item.status != WorkStatus.SUCCEEDED or not item.result:
            payload.update(
                {
                    "result_type": failure_type,
                    "error_code": f"work_item_{item.status}",
                    "message": item.error or f"work item ended as {item.status}",
                }
            )
            payload.pop("MQL", None)
        return payload


def load_results(settings: Settings, store: RunStore, run_id: str) -> dict[str, Any] | None:
    state = store.get_evaluation(run_id)
    if state is None:
        return None
    reports: dict[str, Any] = {}
    for track, paths in state.artifacts.items():
        report_path = _safe_artifact(settings, run_id, paths.get("report_json"))
        if report_path and report_path.is_file():
            report = json.loads(report_path.read_text(encoding="utf-8"))
            record_path = _safe_artifact(settings, run_id, paths.get("per_record_jsonl"))
            report["system_slice_aggregates"] = _system_slice_aggregates(record_path)
            reports[track] = report
    return {**state.model_dump(mode="json"), "reports": reports}


def load_result_records(
    settings: Settings,
    store: RunStore,
    run_id: str,
    *,
    track: str,
    system_id: str | None,
    db_id: str | None,
    outcome: str | None,
    offset: int,
    limit: int,
) -> dict[str, Any] | None:
    state = store.get_evaluation(run_id)
    if state is None:
        return None
    paths = state.artifacts.get(track) or {}
    record_path = _safe_artifact(settings, run_id, paths.get("per_record_jsonl"))
    if record_path is None or not record_path.is_file():
        return {"total": 0, "offset": offset, "limit": limit, "items": []}
    matches: list[dict[str, Any]] = []
    total = 0
    with record_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if system_id and row.get("system_id") != system_id:
                continue
            if db_id and row.get("db_id") != db_id:
                continue
            if outcome and row.get("outcome") != outcome:
                continue
            if total >= offset and len(matches) < limit:
                matches.append(row)
            total += 1
    return {"total": total, "offset": offset, "limit": limit, "items": matches}


def resolve_export(
    settings: Settings, store: RunStore, run_id: str, track: str, kind: str
) -> Path | None:
    if kind not in EXPORT_KINDS:
        return None
    state = store.get_evaluation(run_id)
    if state is None:
        return None
    value = (state.artifacts.get(track) or {}).get(kind)
    return _safe_artifact(settings, run_id, value)


def _safe_artifact(settings: Settings, run_id: str, value: str | None) -> Path | None:
    if not value:
        return None
    root = (settings.runtime_dir / "runs" / run_id).resolve()
    candidate = Path(value).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def _system_slice_aggregates(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    totals: dict[str, dict[str, dict[str, dict[str, float]]]] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            system_id = str(row.get("system_id") or "unknown")
            metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
            slices = row.get("slice_keys") if isinstance(row.get("slice_keys"), dict) else {}
            for axis, raw_value in slices.items():
                value = str(raw_value)
                bucket = (
                    totals.setdefault(system_id, {})
                    .setdefault(str(axis), {})
                    .setdefault(value, {"record_count": 0.0, "EXC": 0.0, "EXF1": 0.0})
                )
                bucket["record_count"] += 1
                bucket["EXC"] += float(metrics.get("EXC", 0))
                bucket["EXF1"] += float(metrics.get("EXF1", 0))
    return {
        system_id: {
            axis: {
                value: {
                    "record_count": int(bucket["record_count"]),
                    "scores": {
                        "EXC": round(bucket["EXC"] / bucket["record_count"], 6),
                        "EXF1": round(bucket["EXF1"] / bucket["record_count"], 6),
                    },
                }
                for value, bucket in sorted(values.items())
            }
            for axis, values in sorted(axes.items())
        }
        for system_id, axes in sorted(totals.items())
    }
