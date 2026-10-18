#!/usr/bin/env python3
"""Fail-closed IO metric/baseline gates (0942). Thresholds must not be weakened."""
from __future__ import annotations

import argparse
import copy
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shell.bonect_parallel_vm_replay import baseline_gate
from shell.bonect_real_validate_candidates import resolve_runtime_capability


COST_PARAMS = {
    "seq_page_cost": 1.0,
    "random_page_cost": 4.0,
    "cpu_tuple_cost": 0.01,
    "cpu_index_tuple_cost": 0.005,
    "cpu_operator_cost": 0.0025,
    "parallel_setup_cost": 1000.0,
    "parallel_tuple_cost": 0.1,
}


def _io_worker() -> dict:
    return {
        "worker_id": "w0",
        "vm_resource": {
            "vm_name": "bonect-replay-v87-3",
            "disk_dev": "vda",
            "read_mb": 10,
            "write_mb": 80,
        },
        "scenario_configs": {"io": str(ROOT / "config/optimizer_config.json")},
        "config_override": {
            "metrics": {
                "vm_runtime": {
                    "cpu_mode": "vm_busy",
                    "memory_mode": "vm_used",
                    "io_mode": "disk_device",
                }
            },
            "scoring": {"overload_threshold": 0.8},
        },
    }


def _io_baseline_row(
    *,
    io_util: float,
    cpu_util: float = 10.0,
    memory_util: float = 40.0,
    samples: int = 50,
    io_mib_s: float = 72.0,
    capability_mismatch: bool = False,
    io_warning: str = "",
    disk_devices: str = "vda",
) -> dict:
    return {
        "candidate_id": "__baseline__w0",
        "params": dict(COST_PARAMS),
        "status": "ok",
        "db_delta": {"db_qps": 1.0},
        "actual_resource": {
            "cpu": cpu_util / 100.0 * 13.0,
            "memory": memory_util / 100.0 * 2.88,
            "io": io_mib_s,
        },
        "actual_utilization_pct": {
            "cpu": cpu_util,
            "memory": memory_util,
            "io": io_util,
        },
        "io_capability_basis": {
            "mode": "configured_total",
            "configured_io_mib_s": 90.0,
            "read_cap_mib_s": 10.0,
            "write_cap_mib_s": 80.0,
            "capability_mismatch": capability_mismatch,
            "capability_mismatch_reason": "test_mismatch" if capability_mismatch else "",
        },
        "raw_resource_stats": {
            "stable_sample_count": {
                "cpu": samples,
                "memory": samples,
                "io": samples,
            },
            "workload_exit": {"returncode": 0},
            "cpu_source": {"value": "vm_runtime_ssh"},
            "memory_source": {"value": "vm_meminfo_used"},
            "io_source": {"value": "vm_disk_device"},
            "io_disk_devices": {"value": disk_devices},
            "io_metric_warning": {"value": io_warning},
        },
    }


def _args() -> argparse.Namespace:
    return argparse.Namespace(
        max_baseline_qps_drift_pct=5.0,
        max_baseline_resource_drift_pp=3.0,
        scenario="io",
        min_stable_samples=45,
        io_baseline_min_pct=80.0,
        io_baseline_max_pct=95.0,
        io_non_target_max_pct=78.0,
        diagnostic_local_pair=False,
    )


def main() -> None:
    workers = [_io_worker()]
    args = _args()

    # 1) below 80% → fail-closed
    below = _io_baseline_row(io_util=79.9, io_mib_s=71.9)
    gate_below = baseline_gate({"w0": [below]}, workers, args)
    assert not gate_below["passed"], gate_below
    assert any("baseline_io_below_80" in f for f in gate_below["failures"]), gate_below

    # 2) inside 80–95% with samples>=45 → pass
    inside = _io_baseline_row(io_util=87.5, io_mib_s=78.75)
    gate_inside = baseline_gate({"w0": [inside]}, workers, args)
    assert gate_inside["passed"], gate_inside
    assert gate_inside["thresholds"]["min_stable_samples"] == 45
    assert gate_inside["thresholds"]["io_baseline_min_pct"] == 80.0
    assert gate_inside["thresholds"]["io_baseline_max_pct"] == 95.0

    # 3) above 95% → fail-closed (historical 177–180% must not pass)
    above = _io_baseline_row(io_util=180.0, io_mib_s=144.0)
    gate_above = baseline_gate({"w0": [above]}, workers, args)
    assert not gate_above["passed"], gate_above
    assert any("baseline_io_above_95_preferred_band" in f for f in gate_above["failures"]), gate_above

    # 4) sample count below 45 → fail-closed
    sparse = _io_baseline_row(io_util=87.5, samples=7)
    gate_sparse = baseline_gate({"w0": [sparse]}, workers, args)
    assert not gate_sparse["passed"], gate_sparse
    assert any("samples_below_45" in f for f in gate_sparse["failures"]), gate_sparse

    # 5) capability mismatch → fail-closed
    mismatch_row = _io_baseline_row(io_util=87.5, capability_mismatch=True)
    gate_mismatch = baseline_gate({"w0": [mismatch_row]}, workers, args)
    assert not gate_mismatch["passed"], gate_mismatch
    assert any("capability_mismatch" in f for f in gate_mismatch["failures"]), gate_mismatch

    # resolve_runtime_capability: configured_total must match directional sum/max
    cap_ok, basis_ok = resolve_runtime_capability(
        {
            "capability": {"cpu": 13, "memory": 2.88, "io": 90},
            "metrics": {"vm_runtime": {"io_capability_mode": "configured_total"}},
        },
        {"read_mb": 10, "write_mb": 80},
    )
    assert cap_ok["io"] == 90
    assert basis_ok["read_cap_mib_s"] == 10
    assert basis_ok["write_cap_mib_s"] == 80
    assert basis_ok["aggregate_definition"] == "configured_total"
    assert basis_ok["capability_mismatch"] is False

    try:
        resolve_runtime_capability(
            {
                "capability": {"cpu": 13, "memory": 2.88, "io": 80},
                "metrics": {"vm_runtime": {"io_capability_mode": "configured_total"}},
            },
            {"read_mb": 10, "write_mb": 80},
        )
    except ValueError as exc:
        assert "IO capability mismatch" in str(exc), exc
    else:
        raise AssertionError("configured_total=80 with read10+write80 must fail")

    # IO warning / zero IO / wrong device still fail
    warn_row = _io_baseline_row(io_util=87.5, io_warning="pg_proc_io_unreadable_fell_back_to_disk_device")
    gate_warn = baseline_gate({"w0": [warn_row]}, workers, args)
    assert not gate_warn["passed"]
    zero_row = _io_baseline_row(io_util=87.5, io_mib_s=0.0)
    gate_zero = baseline_gate({"w0": [zero_row]}, workers, args)
    assert not gate_zero["passed"]
    bad_dev = _io_baseline_row(io_util=87.5, disk_devices="vdb")
    gate_dev = baseline_gate({"w0": [bad_dev]}, workers, args)
    assert not gate_dev["passed"]

    # Non-target CPU/memory must stay below 78% for formal IO baseline
    hot_cpu = _io_baseline_row(io_util=87.5, cpu_util=78.0)
    gate_hot = baseline_gate({"w0": [hot_cpu]}, workers, args)
    assert not gate_hot["passed"]
    assert "baseline_non_target_bottleneck:w0:cpu" in gate_hot["failures"]

    print("io metric gate fail-closed tests passed", flush=True)


if __name__ == "__main__":
    main()
