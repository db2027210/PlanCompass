#!/usr/bin/env python3
"""Stable process entry point for an isolated PostgreSQL Algorithm C lane.

The implementation remains in ``run_pg_algorithm_c_calibrate_then_scene`` so
its source manifest and calibration compatibility stay unchanged.  A neutral
entry-point name prevents unrelated process-name based supervisors from
rewriting the lane's predeclared CPU affinity.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path


SOURCE_RELATIVE_PATHS = (
    "algorithm_c_calibration_release.py",
    "binary_plan_resource_tuner.py",
    "bonect_interval_lineage.py",
    "common_outer_loop.py",
    "experience_table_subset_optimizer.py",
    "extract_plan_trees.py",
    "gradient_descent_optimizer.py",
    "logger_utils.py",
    "operator_resource_rules.py",
    "plan_resource_estimator.py",
    "resource_feature_predictor.py",
    "resource_utility.py",
    "same_ssd_concurrency.py",
    "system_resource_monitor.py",
    "shell/algorithm_c_parameter_support.py",
    "shell/build_algorithm_c_experience_table_from_replays.py",
    "shell/launch_pg_algorithm_c_four_experience_sgd_20260809.py",
    "shell/launch_pg_algorithm_c_four_parallel2_20260809.py",
    "shell/pg_algorithm_c_lane_entry.py",
    "shell/run_benchmark_capture.py",
    "shell/run_job_fixed_benchmark_capture.py",
    "shell/run_hybench_native_xp_isolated.sh",
    "shell/run_pg_algorithm_c_calibrate_then_scene.py",
    "shell/run_pg_algorithm_c_scene.py",
    "shell/run_pg_full_workload_isolated.sh",
    "shell/run_pg_job_fixed_workload_isolated.sh",
    "tools/bonect_fadvise_dontneed.c",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _argument_value(name: str) -> str:
    try:
        return sys.argv[sys.argv.index(name) + 1]
    except (ValueError, IndexError) as exc:
        raise RuntimeError(f"missing required lane-entry argument: {name}") from exc


def verify_runtime_source_provenance() -> None:
    """Verify prepared bytes before importing any optimizer implementation."""

    root = Path(__file__).resolve().parents[1]
    output_dir = Path(_argument_value("--output-dir")).resolve()
    manifest_path = output_dir.parent / "launch_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    declared = ((manifest.get("source_provenance") or {}).get("files") or {})
    expected_names = set(SOURCE_RELATIVE_PATHS)
    declared_names = {str(name) for name in declared}
    missing_declarations = sorted(expected_names - declared_names)
    extra_declarations = sorted(declared_names - expected_names)
    missing_files = []
    mismatches = {}
    observed = {}
    for relative_name in sorted(expected_names):
        path = root / relative_name
        if not path.is_file():
            missing_files.append(relative_name)
            continue
        observed_sha = _sha256(path)
        observed[relative_name] = observed_sha
        expected_sha = str(declared.get(relative_name) or "")
        if observed_sha != expected_sha:
            mismatches[relative_name] = {
                "expected": expected_sha,
                "observed": observed_sha,
            }
    passed = not (
        missing_declarations or extra_declarations or missing_files or mismatches
    )
    readback = {
        "contract": "algorithm_c_lane_entry_source_sha256_readback_v2",
        "checked_at_epoch_s": time.time(),
        "passed": passed,
        "expected_file_count": len(expected_names),
        "missing_declarations": missing_declarations,
        "extra_declarations": extra_declarations,
        "missing_files": missing_files,
        "mismatches": mismatches,
        "observed": observed,
        "physical_workload_invocations": 0,
        "database_mutation": False,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "runtime_source_provenance_readback.json").write_text(
        json.dumps(readback, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if not passed:
        raise RuntimeError(f"Algorithm C runtime source provenance mismatch: {readback}")


if __name__ == "__main__":
    verify_runtime_source_provenance()
    from run_pg_algorithm_c_calibrate_then_scene import main

    raise SystemExit(main())
