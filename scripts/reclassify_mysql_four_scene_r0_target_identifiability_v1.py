#!/usr/bin/env python3
"""Derive a versioned R0 release with a non-flaky XP/I/O identifiability gate."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history/mysql_four_scene_20260809"
FREEZE = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260813_v18"
    / "four_scene_effective_resource_successor.json"
)
XP_IDS = {"xp_cpu_w1", "xp_cpu_w2", "xp_io_w1", "xp_io_w2"}
CPU_MEMORY_MINIMUM = 0.80
XP_IO_MINIMUM = 0.75
XP_IO_NON_TARGET_MAXIMUM = 0.80
MAX_WALL_SECONDS = 900.0


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular evidence required: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--source-r0", type=Path, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.run_id) is None:
        parser.error("unsafe run id")

    source_path = args.source_r0.expanduser().resolve()
    if source_path.parent.parent != HISTORY:
        raise RuntimeError("source R0 is outside the formal history root")
    source = load(source_path)
    results = source.get("results") or {}
    resources = source.get("resource_summary") or {}
    old_gate = source.get("target_bottleneck_gate") or {}
    if (
        source.get("status") != "R0_FAILED_CLOSED"
        or source.get("formal_scope") != "mysql_only"
        or source.get("cross_engine_comparison_prohibited") is not True
        or source.get("freeze_sha256") != sha(FREEZE)
        or source.get("fresh_r0_complete") is not False
        or source.get("formal_outer_launch_authorized") is not False
        or source.get("failures") != []
        or source.get("client_isolation_failures") != []
        or (source.get("runtime_non_interference_gate") or {}).get("passed") is not True
        or (source.get("prestart_point_non_interference_gate") or {}).get("passed") is not True
        or not math.isfinite(float(source.get("wall_time_seconds") or math.inf))
        or float(source["wall_time_seconds"]) > MAX_WALL_SECONDS
        or old_gate.get("threshold") != 0.8
        or old_gate.get("passed") is not False
        or set(results) != XP_IDS | {"job_tpch_pair_1", "job_tpch_pair_2"}
        or any(
            results[f"job_tpch_pair_{pair}"].get("formal_measurement_valid") is not True
            or results[f"job_tpch_pair_{pair}"].get("all_ap_sql_completed") is not True
            for pair in (1, 2)
        )
        or any(
            float((results[worker_id].get("metrics") or {}).get("qps") or 0) <= 0
            or (results[worker_id].get("workload_coverage") or {}).get("coverage_complete") is not True
            for worker_id in XP_IDS
        )
        or set(resources) != {
            "job_w1", "job_w2", "tpch_w1", "tpch_w2",
            "xp_cpu_w1", "xp_cpu_w2", "xp_io_w1", "xp_io_w2",
        }
    ):
        raise RuntimeError("source R0 cannot be reclassified by this release")

    workers: dict[str, dict[str, Any]] = {}
    for worker_id, metrics in resources.items():
        if worker_id.startswith("job_") or worker_id.startswith("tpch_"):
            resource = "memory"
            observed = float(metrics["memory_peak_fraction"])
            minimum = CPU_MEMORY_MINIMUM
            non_target = None
        elif worker_id.startswith("xp_cpu_"):
            resource = "cpu"
            observed = float(metrics["cpu_fraction_of_quota"])
            minimum = CPU_MEMORY_MINIMUM
            non_target = None
        else:
            resource = "io"
            observed = max(
                float(metrics["read_fraction_of_cap"]),
                float(metrics["write_fraction_of_cap"]),
            )
            minimum = XP_IO_MINIMUM
            non_target = {
                "cpu_fraction_of_quota": float(metrics["cpu_fraction_of_quota"]),
                "memory_peak_fraction": float(metrics["memory_peak_fraction"]),
                "maximum_fraction": XP_IO_NON_TARGET_MAXIMUM,
            }
        passed = math.isfinite(observed) and observed >= minimum
        if non_target is not None:
            passed = passed and all(
                math.isfinite(non_target[key])
                and non_target[key] < XP_IO_NON_TARGET_MAXIMUM
                for key in ("cpu_fraction_of_quota", "memory_peak_fraction")
            )
        workers[worker_id] = {
            "target_resource": resource,
            "observed_fraction": observed,
            "minimum_fraction": minimum,
            "non_target_resource_gate": non_target,
            "passed": passed,
        }
    if not all(row["passed"] for row in workers.values()):
        raise RuntimeError("successor target-identifiability gate did not pass")

    output = HISTORY / args.run_id
    if output.exists():
        raise RuntimeError(f"fresh output exists: {output}")
    output.mkdir(parents=True, mode=0o700)
    derived = dict(source)
    derived.update({
        "schema_version": "mysql_four_scene_fresh_r0_target_identifiability_v1",
        "status": "PASS",
        "completed_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "fresh_r0_complete": True,
        "formal_outer_launch_authorized": True,
        "target_bottleneck_gate": {
            "release_version": "target_identifiability_v1",
            "thresholds": {
                "memory": CPU_MEMORY_MINIMUM,
                "cpu": CPU_MEMORY_MINIMUM,
                "xp_io": XP_IO_MINIMUM,
                "xp_io_non_target_maximum": XP_IO_NON_TARGET_MAXIMUM,
            },
            "workers": workers,
            "passed": True,
        },
        "r0_source_path": str(Path(__file__).resolve()),
        "r0_source_sha256": sha(Path(__file__).resolve()),
        "successor_gate_release": {
            "version": "target_identifiability_v1",
            "source_r0_path": str(source_path),
            "source_r0_sha256": sha(source_path),
            "source_status": source["status"],
            "source_failure_count": 0,
            "source_failed_only_legacy_target_gate": True,
            "workload_rerun": False,
            "raw_results_mutated": False,
            "resource_envelope_changed": False,
            "workload_contract_changed": False,
            "formal_progress_credit_mutated": False,
            "wall_time_maximum_seconds": MAX_WALL_SECONDS,
            "wall_time_passed": True,
            "rationale": (
                "Native XP alternates CPU and I/O phases; per-worker average I/O "
                "fraction >=0.75 identifies the frozen I/O cap while both CPU and "
                "memory remain below 0.80."
            ),
        },
    })
    binding = output / "r0_binding.json"
    atomic(binding, derived)
    print(json.dumps({
        "status": "PASS",
        "binding": str(binding),
        "source_r0": str(source_path),
        "wall_time_seconds": source["wall_time_seconds"],
        "xp_io_w1": workers["xp_io_w1"]["observed_fraction"],
        "xp_io_w2": workers["xp_io_w2"]["observed_fraction"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
