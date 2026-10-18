#!/usr/bin/env python3
"""Seal a compact JOB rank-1 model-metadata schema sample for pruning audit."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, Mapping


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ranking", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    ranking_path = args.ranking.resolve()
    output = args.output.resolve()
    if output.exists() or output.is_symlink():
        raise RuntimeError(f"refusing to overwrite metadata sample: {output}")
    ranking = json.loads(ranking_path.read_text(encoding="utf-8"))
    candidates = ranking.get("candidate_rankings") or []
    candidate = next(item for item in candidates if int(item.get("rank", -1)) == 1)
    metadata = candidate.get("final_estimation_metadata") or {}
    logs = metadata.get("resource_estimation_logs") or []
    compact: Dict[str, Any] = {}
    applied_paths_union: Dict[str, str] = {}
    for log_item in logs:
        resource = str(log_item.get("resource_type") or "").strip().lower()
        if resource not in {"cpu", "memory", "io"}:
            continue
        calibration = log_item.get("calibration_breakdown") or {}
        learned = calibration.get("learned_model") or {}
        fallback = calibration.get("learned_model_fallback") or {}
        selected_name = (
            "learned_model"
            if isinstance(learned.get("metrics"), Mapping)
            else "learned_model_fallback"
        )
        selected = learned if selected_name == "learned_model" else fallback
        execution = log_item.get("execution_breakdown") or {}
        workload_context = execution.get("workload_context") or {}
        retraining = workload_context.get("resource_model_retraining") or {}
        applied_paths = retraining.get("applied_model_paths") or {}
        applied_paths_union.update(
            {str(name): str(path) for name, path in applied_paths.items()}
        )
        artifact_path = Path(str(applied_paths.get(resource) or ""))
        test_metrics = ((selected.get("metrics") or {}).get("test") or {})
        compact[resource] = {
            "resource_log_keys": sorted(log_item),
            "calibration_breakdown_keys": sorted(calibration),
            "learned_model_keys": sorted(learned),
            "learned_model_fallback_keys": sorted(fallback),
            "selected_metrics_source": selected_name,
            "selected_model_keys": sorted(selected),
            "target_unit": selected.get("target_unit"),
            "prediction_unit": selected.get("prediction_unit"),
            "prediction_window_seconds": selected.get("prediction_window_seconds"),
            "heldout_test": {
                name: test_metrics.get(name)
                for name in (
                    "sample_count",
                    "abs_error_max",
                    "abs_error_max_utilization_pp",
                    "abs_error_p95",
                    "abs_error_p95_utilization_pp",
                )
            },
            "applied_error_bound": selected.get("error_bound"),
            "resolved_applied_model_path": str(artifact_path),
            "resolved_applied_model_exists": artifact_path.is_file(),
            "resolved_applied_model_sha256": (
                sha256(artifact_path) if artifact_path.is_file() else None
            ),
        }
    if set(compact) != {"cpu", "memory", "io"}:
        raise RuntimeError(f"rank-1 metadata lacks three resources: {sorted(compact)}")
    artifact = {
        "schema_version": 1,
        "artifact_id": "job_round1_rank1_pruning_metadata_schema_sample_v1",
        "source_ranking_path": str(ranking_path),
        "source_ranking_sha256": sha256(ranking_path),
        "candidate_rank": 1,
        "candidate_subset_id": candidate.get("subset_id"),
        "candidate_params": candidate.get("optimized_params"),
        "candidate_final_resource": candidate.get("final_resource"),
        "resource_metadata": compact,
        "applied_model_paths": dict(sorted(applied_paths_union.items())),
        "reader_policy": {
            "metrics_precedence": ["learned_model", "learned_model_fallback"],
            "required_error_metric": "metrics.test.abs_error_max",
            "artifact_path_fallback": (
                "execution_breakdown.workload_context.resource_model_retraining."
                "applied_model_paths[resource]"
            ),
            "simultaneous_box": (
                "per-resource max(heldout abs_error_max, applied/baseline bound)"
            ),
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    with temporary.open("x", encoding="utf-8") as handle:
        json.dump(artifact, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(output)
    print(
        json.dumps(
            {
                "path": str(output),
                "sha256": sha256(output),
                "resources": sorted(compact),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
