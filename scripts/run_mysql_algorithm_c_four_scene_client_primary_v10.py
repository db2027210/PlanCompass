#!/usr/bin/env python3
"""Recover XP-CPU C with a scoped settle gate and its completed baseline."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import shutil
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_async_xp_post_reset_settle_v2 as scoped_settle
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v9 as predecessor
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v32 as boundary_v32


SOURCE_RUN = ROOT / (
    "history/mysql_four_scene_20260809/"
    "v1202_to_c_xp_cpu_client_primary_v99_20260904_xp_cpu"
)
SOURCE_STATE = SOURCE_RUN / "run_state.json"
SOURCE_BASELINE_DIR = SOURCE_RUN / "formal_incumbent_baseline"
SOURCE_BASELINE = SOURCE_BASELINE_DIR / "batch_result.json"
SOURCE_BASELINE_GATE = SOURCE_RUN / (
    "formal_incumbent_baseline_dynamic_noninterference_gate.json"
)
SOURCE_STATE_SHA256 = (
    "d4a6604fe0714e04be73a88530c08b10b477801a24c0b0c5d9a30661f98f1cb4"
)
SOURCE_BASELINE_SHA256 = (
    "96652d26d610cfd95dedbd54d2fa29d472af55ec2b7c05ac15a08b9c7f57a03e"
)
EXPECTED_WORKERS = {"xp_cpu_w1", "xp_cpu_w2"}
EXPECTED_ERROR = "RuntimeError: disk2 post-reset writeback did not settle in 120s"
EXPECTED_RUNTIME_SYMLINKS = {
    f"{worker}/hybench_runtime/Data_10x": "/home/anonymous/HyBench-2024-master/Data_10x"
    for worker in EXPECTED_WORKERS
} | {
    f"{worker}/hybench_runtime/lib": "/home/anonymous/HyBench-2024-master/lib"
    for worker in EXPECTED_WORKERS
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def validate_recovery_source() -> dict[str, Any]:
    if (
        not SOURCE_STATE.is_file()
        or SOURCE_STATE.is_symlink()
        or sha(SOURCE_STATE) != SOURCE_STATE_SHA256
        or not SOURCE_BASELINE.is_file()
        or SOURCE_BASELINE.is_symlink()
        or sha(SOURCE_BASELINE) != SOURCE_BASELINE_SHA256
        or not SOURCE_BASELINE_GATE.is_file()
        or SOURCE_BASELINE_GATE.is_symlink()
    ):
        raise RuntimeError("XP-CPU C recovery source binding differs")
    runtime_symlinks = {
        str(path.relative_to(SOURCE_BASELINE_DIR)): str(path.readlink())
        for path in SOURCE_BASELINE_DIR.rglob("*") if path.is_symlink()
    }
    if runtime_symlinks != EXPECTED_RUNTIME_SYMLINKS:
        raise RuntimeError("XP-CPU C baseline runtime symlink set differs")
    state = load(SOURCE_STATE)
    baseline = load(SOURCE_BASELINE)
    gate = load(SOURCE_BASELINE_GATE)
    rows = dict(baseline.get("measurements") or {})
    if (
        state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or state.get("error") != EXPECTED_ERROR
        or int(state.get("outer_attempt") or 0) != 0
        or any((state.get("accepted_rounds") or {}).values())
        or any((state.get("rejected_outer_attempts") or {}).values())
        or (SOURCE_RUN / "outer_001/top5_batch_1/batch_result.json").exists()
        or baseline.get("status") != "PASS"
        or baseline.get("failures") not in ([], None)
        or set(rows) != EXPECTED_WORKERS
        or any(
            not isinstance(row, Mapping)
            or row.get("scene") != "hybench_xp_cpu"
            or int(row.get("rank") or 0) != 0
            or row.get("hard_gate_passed") is not True
            or row.get("coverage_complete") is not True
            for row in rows.values()
        )
        or gate.get("status") != "PASS"
        or gate.get("errors") not in ([], None)
    ):
        raise RuntimeError("XP-CPU C failed boundary or completed baseline differs")
    binding = dict(state.get("formal_incumbent_baseline") or {})
    if (
        binding.get("status") != "PASS"
        or Path(str(binding.get("path") or "")).resolve() != SOURCE_BASELINE.resolve()
        or binding.get("sha256") != SOURCE_BASELINE_SHA256
        or int(binding.get("worker_count") or 0) != 2
        or int(binding.get("lanes_per_scene") or 0) != 2
        or binding.get("accepted_rounds_mutated") is not False
    ):
        raise RuntimeError("XP-CPU C completed baseline state binding differs")
    return baseline


def _copy_completed_baseline(
    *, baseline: Mapping[str, Any], original_measure: Any, **kwargs: Any,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    if kwargs.get("stage_prefix") != "FORMAL_INCUMBENT_BASELINE":
        return original_measure(**kwargs)
    output = Path(kwargs["output"]).resolve()
    state_path = Path(kwargs["state_path"]).resolve()
    state = copy.deepcopy(dict(kwargs["state"]))
    assignments = dict(kwargs.get("assignments") or {})
    workers = dict(kwargs.get("workers") or {})
    rows = copy.deepcopy(dict(baseline.get("measurements") or {}))
    if (
        output.name != "formal_incumbent_baseline"
        or output.exists()
        or output.is_symlink()
        or set(assignments) != EXPECTED_WORKERS
        or set(workers) != EXPECTED_WORKERS
        or set(rows) != EXPECTED_WORKERS
    ):
        raise RuntimeError("XP-CPU C baseline recovery destination differs")
    alg = boundary_v32.runtime.alg
    for worker_id in sorted(EXPECTED_WORKERS):
        assignment = dict(assignments[worker_id])
        row = dict(rows[worker_id])
        worker = dict(workers[worker_id])
        if (
            worker.get("scene") != "hybench_xp_cpu"
            or int(assignment.get("rank") or 0) != 0
            or alg.optimizer_params_key(dict(assignment.get("params") or {}))
            != alg.optimizer_params_key(dict(row.get("params") or {}))
        ):
            raise RuntimeError(f"XP-CPU C baseline recovery row differs: {worker_id}")
    shutil.copytree(
        SOURCE_BASELINE_DIR, output, copy_function=shutil.copy2, symlinks=True,
    )
    if sha(output / "batch_result.json") != SOURCE_BASELINE_SHA256:
        raise RuntimeError("copied XP-CPU C baseline binding differs")
    reuse = {
        "schema_version": "mysql_c_xp_cpu_completed_baseline_reuse_v1",
        "status": "PASS",
        "created_at": alg.now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "source_run_state_path": str(SOURCE_STATE),
        "source_run_state_sha256": SOURCE_STATE_SHA256,
        "source_baseline_path": str(SOURCE_BASELINE),
        "source_baseline_sha256": SOURCE_BASELINE_SHA256,
        "source_dynamic_gate_path": str(SOURCE_BASELINE_GATE),
        "source_dynamic_gate_sha256": sha(SOURCE_BASELINE_GATE),
        "destination_baseline_path": str(output / "batch_result.json"),
        "worker_ids": sorted(EXPECTED_WORKERS),
        "physical_measurement_replayed": False,
        "candidate_measurement_reused": False,
        "accepted_or_rejected_credit_mutated": False,
        "workload_or_resource_envelope_changed": False,
    }
    alg.atomic(output / "baseline_reuse.json", reuse)
    state["formal_baseline_recovery_reuse"] = copy.deepcopy(reuse)
    state["stage"] = "FORMAL_INCUMBENT_BASELINE_REUSED"
    alg.atomic(state_path, state)
    return rows, state


def main() -> int:
    baseline = validate_recovery_source()
    original_boundary_main = boundary_v32.main

    def recovered_boundary_main() -> int:
        runtime = boundary_v32.runtime
        original_measure = runtime._measure_batch_with_fair_coordination
        original_settle = boundary_v32.reset_xp

        def recovered_measure(**kwargs: Any):
            return _copy_completed_baseline(
                baseline=baseline, original_measure=original_measure, **kwargs,
            )

        runtime._measure_batch_with_fair_coordination = recovered_measure
        boundary_v32.reset_xp = scoped_settle.reset_xp
        try:
            return int(original_boundary_main())
        finally:
            boundary_v32.reset_xp = original_settle
            runtime._measure_batch_with_fair_coordination = original_measure

    boundary_v32.main = recovered_boundary_main
    try:
        return int(predecessor.main())
    finally:
        boundary_v32.main = original_boundary_main


if __name__ == "__main__":
    raise SystemExit(main())
