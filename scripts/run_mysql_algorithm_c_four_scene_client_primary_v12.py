#!/usr/bin/env python3
"""Recover XP-I/O C by retaining the passed baseline lane and retrying only w2."""

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

from scripts import run_mysql_algorithm_c_four_scene_client_primary_v11 as predecessor
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v32 as boundary_v32


SOURCE_RUN = ROOT / (
    "history/mysql_four_scene_20260809/"
    "v1206_to_c_xp_io_client_primary_v101_20260904_xp_io"
)
SOURCE_STATE = SOURCE_RUN / "run_state.json"
SOURCE_BASELINE_DIR = SOURCE_RUN / "formal_incumbent_baseline"
SOURCE_BASELINE = SOURCE_BASELINE_DIR / "batch_result.json"
SOURCE_GATE = SOURCE_RUN / "formal_incumbent_baseline_dynamic_noninterference_gate.json"
SOURCE_STATE_SHA256 = "cbcb6423195defc33dc6b1e03886729147246ee355f641d8d2701d44859bc38a"
SOURCE_BASELINE_SHA256 = "7d66af6698dc05a05530f83618816429dbc5d8ae5056368d619d20145820a664"
SOURCE_GATE_SHA256 = "71b5411c515d25e8c083e69732e283abe8d526b98435f37dc62e29288346fe27"
RETAINED_WORKER = "xp_io_w1"
RETRY_WORKER = "xp_io_w2"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def validate_recovery_source() -> dict[str, Any]:
    if (
        sha(SOURCE_STATE) != SOURCE_STATE_SHA256
        or sha(SOURCE_BASELINE) != SOURCE_BASELINE_SHA256
        or sha(SOURCE_GATE) != SOURCE_GATE_SHA256
    ):
        raise RuntimeError("XP-I/O failed-baseline recovery source binding differs")
    state = load(SOURCE_STATE)
    baseline = load(SOURCE_BASELINE)
    gate = load(SOURCE_GATE)
    rows = dict(baseline.get("measurements") or {})
    retained = dict(rows.get(RETAINED_WORKER) or {})
    retry = dict(rows.get(RETRY_WORKER) or {})
    stability = dict(retained.get("resource_stability") or {})
    if (
        state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or state.get("error") != "RuntimeError: formal incumbent baseline failed closed"
        or int(state.get("outer_attempt") or 0) != 0
        or any((state.get("accepted_rounds") or {}).values())
        or any((state.get("rejected_outer_attempts") or {}).values())
        or baseline.get("status") != "FAILED_CLOSED"
        or baseline.get("failures") != []
        or set(rows) != {RETAINED_WORKER, RETRY_WORKER}
        or retained.get("hard_gate_passed") is not True
        or retained.get("coverage_complete") is not True
        or stability.get("stable") is not True
        or int(stability.get("selected_measurement_seconds") or 0) != 540
        or retry.get("hard_gate_passed") is not False
        or (retry.get("hard_gate_checks") or {}).get("target_resource_stability_passed")
        is not False
        or gate.get("status") != "PASS"
    ):
        raise RuntimeError("XP-I/O baseline is not the exact one-lane stability failure")
    return retained


def recover_baseline_lane(
    *, retained: Mapping[str, Any], original_measure: Any, **kwargs: Any,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    if kwargs.get("stage_prefix") != "FORMAL_INCUMBENT_BASELINE":
        return original_measure(**kwargs)
    output = Path(kwargs["output"]).resolve()
    workers = dict(kwargs["workers"])
    assignments = dict(kwargs["assignments"])
    if (
        output.name != "formal_incumbent_baseline"
        or output.exists()
        or set(workers) != {RETAINED_WORKER, RETRY_WORKER}
        or set(assignments) != {RETAINED_WORKER, RETRY_WORKER}
    ):
        raise RuntimeError("XP-I/O baseline lane-recovery destination differs")

    retry_kwargs = dict(kwargs)
    retry_kwargs["workers"] = {RETRY_WORKER: workers[RETRY_WORKER]}
    retry_kwargs["assignments"] = {RETRY_WORKER: assignments[RETRY_WORKER]}
    measured, state = original_measure(**retry_kwargs)
    if set(measured) != {RETRY_WORKER}:
        raise RuntimeError("XP-I/O retry lane result differs")

    shutil.copytree(
        SOURCE_BASELINE_DIR / RETAINED_WORKER,
        output / RETAINED_WORKER,
        copy_function=shutil.copy2,
        symlinks=True,
    )
    old_samples = SOURCE_BASELINE_DIR / "resource_samples.jsonl"
    retained_samples = output / "retained_xp_io_w1_resource_samples.jsonl"
    with old_samples.open("r", encoding="utf-8") as source, retained_samples.open(
        "x", encoding="utf-8"
    ) as destination:
        for line in source:
            row = json.loads(line)
            if row.get("worker_id") == RETAINED_WORKER:
                destination.write(line)

    alg = boundary_v32.runtime.alg
    batch_path = output / "batch_result.json"
    batch = alg.load(batch_path)
    rows = dict(batch.get("measurements") or {})
    rows[RETAINED_WORKER] = copy.deepcopy(dict(retained))
    batch["measurements"] = rows
    retry_passed = (
        rows[RETRY_WORKER].get("hard_gate_passed") is True
        and rows[RETRY_WORKER].get("coverage_complete") is True
    )
    batch["status"] = "PASS" if retry_passed else "FAILED_CLOSED"
    batch["baseline_lane_recovery"] = {
        "schema_version": "mysql_c_xp_io_single_failed_baseline_lane_recovery_v1",
        "source_baseline_path": str(SOURCE_BASELINE),
        "source_baseline_sha256": SOURCE_BASELINE_SHA256,
        "retained_worker": RETAINED_WORKER,
        "retained_selected_measurement_seconds": 540,
        "retried_worker": RETRY_WORKER,
        "retained_physical_measurement_replayed": False,
        "accepted_or_rejected_credit_mutated": False,
        "stability_threshold_changed": False,
        "native_xp_workload_changed": False,
        "resource_envelope_changed": False,
        "simultaneous_lane_measurement_required": False,
    }
    alg.atomic(batch_path, batch)
    measured[RETAINED_WORKER] = copy.deepcopy(dict(retained))
    state["formal_baseline_lane_recovery"] = copy.deepcopy(
        batch["baseline_lane_recovery"]
    )
    alg.atomic(Path(kwargs["state_path"]), state)
    return measured, state


def main() -> int:
    retained = validate_recovery_source()
    original_boundary_main = boundary_v32.main

    def recovered_boundary_main() -> int:
        runtime = boundary_v32.runtime
        original_measure = runtime._measure_batch_with_fair_coordination

        def recovered_measure(**kwargs: Any):
            return recover_baseline_lane(
                retained=retained, original_measure=original_measure, **kwargs,
            )

        runtime._measure_batch_with_fair_coordination = recovered_measure
        try:
            return int(original_boundary_main())
        finally:
            runtime._measure_batch_with_fair_coordination = original_measure

    boundary_v32.main = recovered_boundary_main
    try:
        return int(predecessor.main())
    finally:
        boundary_v32.main = original_boundary_main


if __name__ == "__main__":
    raise SystemExit(main())
