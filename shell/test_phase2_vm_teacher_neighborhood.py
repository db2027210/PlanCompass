#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "shell") not in sys.path:
    sys.path.insert(0, str(ROOT / "shell"))

from run_bonect_phase2_vm_teacher_neighborhood import (  # noqa: E402
    build_scenario_points,
    measurement_gate,
    params_signature,
)
from run_bonect_phase2_vm_teacher_validation import (  # noqa: E402
    COST_PARAM_KEYS,
    read_json,
)


def test_point_geometry_and_holdout() -> None:
    teacher_manifest = read_json(
        ROOT / "config" / "bonect_phase2_vm_teacher_validation_20260711.json"
    )
    manifest = read_json(
        ROOT / "config" / "bonect_phase2_vm_teacher_neighborhood_20260711.json"
    )
    default = teacher_manifest["default_params"]
    expected_counts = {"cpu": 17, "memory": 9, "io": 9, "none": 18}
    for scenario, expected_count in expected_counts.items():
        teacher = teacher_manifest["scenarios"][scenario]["teacher_params"]
        points = build_scenario_points(
            scenario=scenario,
            default=default,
            teacher=teacher,
            sampling=manifest["sampling"],
            binary_parent=manifest.get("binary_parent_points", {}).get(scenario),
        )
        assert len(points) == expected_count, (scenario, len(points))
        signatures = {params_signature(point["params"]) for point in points}
        assert params_signature(default) not in signatures
        assert params_signature(teacher) not in signatures
        assert len(signatures) == len(points)
        for point in points:
            assert set(point["params"]) == set(COST_PARAM_KEYS)


def test_measurement_gate() -> None:
    gates = {
        "required_io_source": "vm_disk_device",
        "required_io_device": "vda",
        "min_stable_samples": 45,
        "none_baseline_ranges": {
            "cpu_pct": [20.0, 35.0],
            "memory_pct": [15.0, 50.0],
            "io_mib_s": [12.0, 30.0],
        },
    }
    record = {
        "db_qps": 100.0,
        "resource_normalized": {
            "cpu": 2.0,
            "memory": 2.0,
            "io": 20.0,
        },
        "utilization_pct": {
            "cpu": 25.0,
            "memory": 25.0,
            "io": 26.0,
        },
        "resource_sources": {
            "io_source": "vm_disk_device",
            "io_disk_devices": "vda",
        },
        "stable_sample_count": {"cpu": 60, "memory": 60, "io": 60},
        "io_read_mib_s": 8.0,
        "io_write_mib_s": 12.0,
        "generated_initial_param_keys": list(COST_PARAM_KEYS),
    }
    assert measurement_gate(
        record,
        scenario="none",
        gates=gates,
        require_baseline_profile=True,
    )["passed"]
    cpu_bottleneck_record = dict(record)
    cpu_bottleneck_record["resource_normalized"] = dict(record["resource_normalized"])
    cpu_bottleneck_record["utilization_pct"] = {
        "cpu": 90.0,
        "memory": 85.0,
        "io": 26.0,
    }
    assert not measurement_gate(
        cpu_bottleneck_record,
        scenario="cpu",
        gates={
            **gates,
            "single_bottleneck_target_min_util_pct": 80.0,
            "single_bottleneck_non_target_max_util_pct": 79.999,
        },
        require_baseline_profile=True,
    )["passed"]
    record["resource_normalized"]["io"] = 0.0
    assert not measurement_gate(
        record,
        scenario="none",
        gates=gates,
        require_baseline_profile=True,
    )["passed"]


def main() -> None:
    test_point_geometry_and_holdout()
    test_measurement_gate()
    print("PASS Phase2 VM teacher neighborhood tests", flush=True)


if __name__ == "__main__":
    main()
