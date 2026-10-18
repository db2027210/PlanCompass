#!/usr/bin/env python3
"""Collect fresh JOB/Memory coordinate-direction evidence after sealed R0."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping

import run_job_r0_profile_v2_codex as sealed


ROOT = sealed.OUTPUT.parent / "direction_calibration"
PARAMETERS = (
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
)
# This prior only chooses the first sign. Three scopes rotate reverse probes,
# so every parameter obtains observations on both sides of the default.
FIRST_INCREASE = {
    "seq_page_cost": True,
    "random_page_cost": False,
    "cpu_tuple_cost": True,
    "cpu_index_tuple_cost": False,
    "cpu_operator_cost": True,
    "parallel_setup_cost": True,
    "parallel_tuple_cost": True,
}


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def append_jsonl(path: Path, payload: Mapping[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(payload), ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()


def raw_result(result: Mapping[str, Any], params: Mapping[str, float]) -> Dict[str, Any]:
    measurement = result.get("measurement") or {}
    stability = measurement.get("resource_stability") or {}
    cleanup = result.get("cleanup") or {}
    usable = (
        result.get("status") == "complete"
        and stability.get("passed") is True
        and measurement.get("correctness_passed") is True
        and cleanup.get("cleanup_complete") is True
    )
    return {
        "status": "ok" if usable else "failed",
        "double_loop_measurement_usable": usable,
        "workload": "job",
        "target_resource": "memory",
        "scenario": "memory",
        "params": dict(params),
        "db_qps": measurement.get("db_qps"),
        "resource_utilization": measurement.get("resource_mean_utilization"),
        "runtime_capability": {
            "cpu": 3.0,
            "memory": sealed.MEMORY_BYTES / 1024 ** 3,
            "memory_limit_bytes": sealed.MEMORY_BYTES,
            "io_read_cap_mib_s": 128.0,
            "io_write_cap_mib_s": 128.0,
            "io": 256.0,
        },
        "candidate_measurement_protocol": {
            "stable": stability.get("passed") is True,
            "correctness_passed": measurement.get("correctness_passed") is True,
            "cleanup_passed": cleanup.get("cleanup_complete") is True,
            "effective_measurement_seconds": measurement.get(
                "effective_measurement_seconds"
            ),
            "termination_reason": measurement.get("measurement_termination_reason"),
        },
    }


def design(scope: int) -> List[Dict[str, Any]]:
    defaults = sealed.runner.default_cost_parameters()
    factor = (1.25, 1.5, 2.0)[scope - 1]
    rows: List[Dict[str, Any]] = []
    for name in PARAMETERS:
        increase = FIRST_INCREASE[name]
        params = dict(defaults)
        params[name] *= factor if increase else 1.0 / factor
        rows.append(
            {
                "parameter": name,
                "direction": "increase" if increase else "decrease",
                "params": params,
            }
        )
    reverse_names = [
        PARAMETERS[(3 * (scope - 1) + offset) % len(PARAMETERS)]
        for offset in range(3)
    ]
    for name in reverse_names:
        increase = not FIRST_INCREASE[name]
        params = dict(defaults)
        params[name] *= factor if increase else 1.0 / factor
        rows.append(
            {
                "parameter": name,
                "direction": "increase" if increase else "decrease",
                "params": params,
            }
        )
    return rows


def calibration_ids(path: Path) -> set:
    ids = set()
    if not path.is_file():
        return ids
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("calibration_id") and row.get("status") in {
            "usable",
            "technical_failure",
        }:
            ids.add(str(row["calibration_id"]))
    return ids


def main() -> int:
    ROOT.mkdir(parents=True, exist_ok=True)
    defaults = sealed.runner.default_cost_parameters()
    for scope in range(1, 4):
        scope_root = ROOT / f"scope_{scope:02d}" / "job" / "memory"
        points_root = scope_root / "points"
        points_root.mkdir(parents=True, exist_ok=True)
        baseline_path = scope_root / "fresh_baseline.json"
        source_path = sealed.OUTPUT / f"repeat_{scope:02d}" / "point_result.json"
        source = json.loads(source_path.read_text(encoding="utf-8"))
        baseline_raw = raw_result(source, defaults)
        if not baseline_raw["double_loop_measurement_usable"]:
            raise RuntimeError(f"sealed R0 source is not usable: {source_path}")
        if not baseline_path.exists():
            write_json(
                baseline_path,
                {
                    "event": "fresh_baseline",
                    "scenario": "memory",
                    "fresh_prefix_source": f"job_direction_calibration_scope_{scope:02d}",
                    "params": defaults,
                    "measurement": {
                        "db_qps": baseline_raw["db_qps"],
                        **dict(baseline_raw["resource_utilization"] or {}),
                    },
                    "raw": baseline_raw,
                    "source_point_result": str(source_path),
                },
            )

        calibration_path = scope_root / "fresh_model_calibration.jsonl"
        completed = calibration_ids(calibration_path)
        for index, candidate in enumerate(design(scope), start=1):
            calibration_id = f"scope{scope:02d}_axis_{index:02d}"
            if calibration_id in completed:
                print(f"[resume] {calibration_id}", flush=True)
                continue
            attempts = []
            usable_raw = None
            for attempt in range(1, 3):
                point_dir = points_root / f"{calibration_id}_attempt_{attempt:02d}"
                result_path = point_dir / "point_result.json"
                if result_path.exists():
                    result = json.loads(result_path.read_text(encoding="utf-8"))
                else:
                    result = sealed.runner.run_cost_point(
                        "pg_job_memory",
                        f"job_direction_{calibration_id}_attempt_{attempt:02d}",
                        scope * 1000 + index * 10 + attempt,
                        point_dir,
                        candidate["params"],
                        evidence_context={
                            "purpose": "job_memory_fresh_direction_calibration",
                            "experiment_scope": "postgresql_pilot_only",
                            "calibration_id": calibration_id,
                            "parameter": candidate["parameter"],
                            "direction": candidate["direction"],
                            "source_r0": str(source_path),
                        },
                        host_lock_already_held=True,
                        warmup_seconds=60,
                        measurement_seconds=600,
                    )
                current = raw_result(result, candidate["params"])
                current["point_result_path"] = str(result_path)
                attempts.append(current)
                if current["double_loop_measurement_usable"]:
                    usable_raw = current
                    break
            row = {
                "event": "fresh_model_calibration_replay",
                "phase": "coordinate_axis_direction_calibration",
                "calibration_id": calibration_id,
                "parameter": candidate["parameter"],
                "direction": candidate["direction"],
                "changed_parameters": [candidate["parameter"]],
                "params": candidate["params"],
                "design": "single_coordinate_current_profile",
                "current_run_only": True,
                "used_for_optimizer_candidate_selection": False,
                "status": "usable" if usable_raw is not None else "technical_failure",
                "attempts": attempts,
            }
            if usable_raw is not None:
                row["raw_result"] = usable_raw
            append_jsonl(calibration_path, row)
            print(
                json.dumps(
                    {
                        "calibration_id": calibration_id,
                        "status": row["status"],
                        "db_qps": usable_raw.get("db_qps") if usable_raw else None,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    write_json(
        ROOT / "calibration_summary.json",
        {
            "status": "complete",
            "scopes": 3,
            "candidates_per_scope": len(design(1)),
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
