#!/usr/bin/env python3
"""Freeze the narrowly recalibrated TPC-H v29 launch artifacts."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
READINESS = ROOT / "history/mysql_gradient_debug/readiness_mysql_early_stop_v1"
BASE_CONFIG = READINESS / "tpch_v28_config_cpu200_io320_parallel4_affinity_v1.json"
BASE_PROFILE = READINESS / "tpch_v28_profile_cpu200_io320_parallel4_affinity_v1.json"
BASE_DRIVER = READINESS / "tpch_v28_driver_cpu200_io320_parallel4_affinity_v2.py"
PROFILE = READINESS / "tpch_v29_profile_cpu190_io320_parallel4_affinity_v1.json"
CONFIG = READINESS / "tpch_v29_config_cpu190_io320_parallel4_affinity_v1.json"
DRIVER = READINESS / "tpch_v29_driver_cpu190_io320_parallel4_affinity_v1.py"
OUTPUT = (
    ROOT
    / "history/mysql_gradient_debug/"
    "tpch_local_r0_v29_cpu190_io320_parallel4_affinity_v1"
)
CPU = 1.9
IO = 335544320


def payload(value: Any) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")


def publish(path: Path, data: bytes) -> None:
    expected = f"{hashlib.sha256(data).hexdigest()}  {path.name}\n"
    sidecar = path.with_name(path.name + ".sha256")
    if path.exists():
        if path.read_bytes() != data or sidecar.read_text() != expected:
            raise RuntimeError(f"existing frozen v29 artifact differs: {path}")
        return
    temporary = path.with_name(f".{path.name}.{os.getpid()}.partial")
    temporary.write_bytes(data)
    os.replace(temporary, path)
    sidecar.write_text(expected, encoding="ascii")
    os.chmod(path, 0o444)
    os.chmod(sidecar, 0o444)


def main() -> int:
    config = json.loads(BASE_CONFIG.read_text())
    source = Path(config["early_stop_source_bundle"]["source"]).resolve(strict=True)
    manifest = json.loads((source / "source_manifest.json").read_text())
    if (
        manifest.get("schema_version") != "mysql_tpch_local_source_v28"
        or manifest.get("database_counter_gate", {}).get(
            "persistent_read_timeout_seconds"
        ) != 3.0
    ):
        raise RuntimeError("TPC-H v29 source authority differs")

    profile = json.loads(BASE_PROFILE.read_text())
    profile["cpu_cores"] = CPU
    profile["derivation"] = {
        "calibration_parent": "tpch_v28_cpu200_io320_parallel4_affinity_v1",
        "v28_latest180": {
            "cpu_ratio": 0.2945345700477622,
            "actual_cpu_cores": 0.5890691400955244,
            "memory_ratio": 0.9040118045387099,
            "io_ratio": 0.4663676779782649,
        },
        "v28_database_counter_gate": {
            "passed": True,
            "sampler_0_coverage": 0.9783333333333334,
            "sampler_1_coverage": 0.995,
            "maximum_gap_ms": 4010,
        },
        "new_cpu_cores": CPU,
        "new_io_read_bps": IO,
        "new_io_write_bps": IO,
        "reason": "v28_cpu_only_0.005465_below_lower_bound_narrow_5pct_quota_adjustment",
        "database_counter_policy": (
            "unchanged_two_staggered_samplers_3s_timeout_90pct_coverage_"
            "10s_gap_fail_closed"
        ),
        "host_affinity_contract": (
            "controller_and_workload_exact_200-207,456-463_fail_closed"
        ),
        "tpch_terminals": 4,
    }

    config["profile"] = str(PROFILE)
    config["output"]["directory"] = str(OUTPUT)
    workload = config["workload"]
    workload["host_affinity_generation"] = (
        "v29_exact_controller_and_workload"
    )
    workload["stability_rationale"] = (
        "v28_narrow_cpu_lower_bound_recalibration; all other gates frozen"
    )
    workload["v19_resource_contract"] = {
        "cpu_quota_cores": CPU,
        "io_read_bps": IO,
        "io_read_mib_per_s": 320,
        "io_write_bps": IO,
        "io_write_mib_per_s": 320,
        "terminals": 4,
    }

    driver = BASE_DRIVER.read_text()
    replacements = {
        '"cpu_quota_cores": 2.0': '"cpu_quota_cores": 1.9',
        '"v28_exact_controller_and_workload"':
            '"v29_exact_controller_and_workload"',
        "v28 resource contract mismatch": "v29 resource contract mismatch",
        "v28_driver_state.json": "v29_driver_state.json",
        "v28_r0_result.json": "v29_r0_result.json",
    }
    for old, new in replacements.items():
        if old not in driver:
            raise RuntimeError(f"v29 driver source marker is absent: {old}")
        driver = driver.replace(old, new)

    publish(PROFILE, payload(profile))
    publish(CONFIG, payload(config))
    publish(DRIVER, driver.encode("utf-8"))
    print(json.dumps({
        "source": str(source),
        "profile": str(PROFILE),
        "config": str(CONFIG),
        "driver": str(DRIVER),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
