#!/usr/bin/env python3
"""Freeze the TPC-H v28 profile, config, and guarded local driver."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
READINESS = ROOT / "history/mysql_gradient_debug/readiness_mysql_early_stop_v1"
BASE_CONFIG = READINESS / "tpch_v27_config_cpu300_io300_parallel4_affinity_v1.json"
BASE_PROFILE = READINESS / "tpch_v27_profile_cpu300_io300_parallel4_affinity_v1.json"
PROFILE = READINESS / "tpch_v28_profile_cpu200_io320_parallel4_affinity_v1.json"
CONFIG = READINESS / "tpch_v28_config_cpu200_io320_parallel4_affinity_v1.json"
DRIVER = READINESS / "tpch_v28_driver_cpu200_io320_parallel4_affinity_v2.py"
OUTPUT = (
    ROOT
    / "history/mysql_gradient_debug/"
    "tpch_local_r0_v28_cpu200_io320_parallel4_affinity_v1"
)
CPU = 2.0
IO = 335544320
CPUSET = "200-207,456-463"


def payload(value: Any) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def publish(path: Path, data: bytes) -> None:
    sidecar = path.with_name(path.name + ".sha256")
    expected_sidecar = f"{sha(data)}  {path.name}\n".encode("ascii")
    if path.exists():
        if path.read_bytes() != data or sidecar.read_bytes() != expected_sidecar:
            raise RuntimeError(f"existing frozen v28 launch artifact differs: {path}")
        return
    temporary = path.with_name(f".{path.name}.{os.getpid()}.partial")
    temporary.write_bytes(data)
    os.replace(temporary, path)
    sidecar.write_bytes(expected_sidecar)
    os.chmod(path, 0o444)
    os.chmod(sidecar, 0o444)


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: prepare_tpch_v28_launch.py SOURCE_V28")
    source = Path(sys.argv[1]).resolve(strict=True)
    manifest = json.loads((source / "source_manifest.json").read_text())
    if (
        manifest.get("schema_version") != "mysql_tpch_local_source_v28"
        or source.name != "source-v28-" + str(manifest.get("bundle_id") or "")
        or manifest.get("database_counter_gate", {}).get(
            "persistent_read_timeout_seconds"
        ) != 3.0
    ):
        raise RuntimeError("v28 source manifest differs")

    profile = json.loads(BASE_PROFILE.read_text())
    profile.update({
        "cpu_cores": CPU,
        "read_bps": IO,
        "write_bps": IO,
        "derivation": {
            "calibration_parents": [
                "tpch_v23_cpu150_io275_parallel4_pinned",
                "tpch_v27_cpu300_io300_parallel4_affinity_v1",
            ],
            "v23_latest180": {
                "cpu_ratio": 0.7818950800663341,
                "actual_cpu_cores": 1.1728426200995012,
                "io_ratio": 0.5002418484279849,
            },
            "v27_latest180": {
                "cpu_ratio": 0.1298716940925651,
                "actual_cpu_cores": 0.38961508227769537,
                "io_ratio": 0.4933414799804336,
            },
            "new_cpu_cores": CPU,
            "new_io_read_bps": IO,
            "new_io_write_bps": IO,
            "reason": (
                "bracket_cpu_between_v23_and_v27_and_raise_io_cap_for_"
                "measurement_margin"
            ),
            "database_counter_policy": (
                "two_staggered_samplers_3s_read_timeout_90pct_coverage_"
                "10s_max_gap_fail_closed"
            ),
            "host_affinity_contract": (
                "controller_and_workload_exact_200-207,456-463_fail_closed"
            ),
            "tpch_terminals": 4,
        },
    })

    config = json.loads(BASE_CONFIG.read_text())
    config["profile"] = str(PROFILE)
    config["output"]["directory"] = str(OUTPUT)
    config["early_stop_source_bundle"]["source"] = str(source)
    workload = config["workload"]
    workload["host_affinity_generation"] = (
        "v28_exact_controller_and_workload"
    )
    workload["stability_rationale"] = (
        "v23_v27_actual_demand_bracket; frozen latest-six gates unchanged"
    )
    workload["v19_resource_contract"] = {
        "cpu_quota_cores": CPU,
        "io_read_bps": IO,
        "io_read_mib_per_s": 320,
        "io_write_bps": IO,
        "io_write_mib_per_s": 320,
        "terminals": 4,
    }
    config["formal_scope"] = "mysql_only"
    config["cross_engine_comparison_prohibited"] = True

    driver = f'''from __future__ import annotations
import hashlib, json, sys, traceback
from pathlib import Path

config_path = Path(sys.argv[1]).resolve()
sidecar = config_path.with_name(config_path.name + ".sha256")
if sidecar.read_text(encoding="ascii") != hashlib.sha256(config_path.read_bytes()).hexdigest() + "  " + config_path.name + "\\n":
    raise RuntimeError("v28 config sidecar differs")
config = json.loads(config_path.read_text(encoding="utf-8"))
workload = config["workload"]
source = Path(config["early_stop_source_bundle"]["source"]).resolve(strict=True)
sys.path.insert(0, str(source))
from mysql_algorithm_c_local_services import build
source_manifest = json.loads((source / "source_manifest.json").read_text(encoding="utf-8"))
if (
    source_manifest.get("schema_version") != "mysql_tpch_local_source_v28"
    or source.name != "source-v28-" + source_manifest.get("bundle_id", "")
    or source_manifest.get("formal_scope") != "mysql_only"
    or source_manifest.get("cross_engine_comparison_prohibited") is not True
    or source_manifest.get("database_counter_gate", {{}}).get("persistent_read_timeout_seconds") != 3.0
):
    raise RuntimeError("v28 source manifest authority differs")
for relative, expected_sha in source_manifest["files"].items():
    candidate = source / relative
    if candidate.is_symlink() or not candidate.is_file() or hashlib.sha256(candidate.read_bytes()).hexdigest() != expected_sha:
        raise RuntimeError(f"v28 source closure mismatch: {{relative}}")
expected = {{"cpu_quota_cores": 2.0, "io_read_bps": 335544320, "io_write_bps": 335544320, "terminals": 4}}
assert workload["host_control_cpuset"] == "200-207,456-463"
assert workload["host_affinity_generation"] == "v28_exact_controller_and_workload"
assert workload["fresh_copy_mode"] == "full_physical_copy_no_overlay_no_reflink"
for key, value in expected.items():
    if workload["v19_resource_contract"].get(key) != value:
        raise RuntimeError(f"v28 resource contract mismatch: {{key}}")
profile_path = Path(config["profile"])
profile_sidecar = profile_path.with_name(profile_path.name + ".sha256")
if profile_sidecar.read_text(encoding="ascii") != hashlib.sha256(profile_path.read_bytes()).hexdigest() + "  " + profile_path.name + "\\n":
    raise RuntimeError("v28 profile sidecar differs")
profile = json.loads(profile_path.read_text(encoding="utf-8"))
assert profile["cpu_cores"] == expected["cpu_quota_cores"]
assert profile["cpuset"] == workload["host_control_cpuset"]
assert profile["read_bps"] == expected["io_read_bps"]
assert profile["write_bps"] == expected["io_write_bps"]
bundle = build(config)
output = Path(config["output"]["directory"])
output.mkdir(parents=True, exist_ok=False)
state_path = output / "v28_driver_state.json"
state = {{"state": "STARTING_FRESH_R0", "config": str(config_path), "resource_contract": expected, "host_control_cpuset": workload["host_control_cpuset"]}}
state_path.write_text(json.dumps(state, sort_keys=True) + "\\n", encoding="utf-8")
try:
    result = bundle["r0_measurement"](config)
    result_path = output / "v28_r0_result.json"
    result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\\n", encoding="utf-8")
    state = {{"state": "R0_COMPLETED", "result": str(result_path), "host_control_cpuset": workload["host_control_cpuset"]}}
except BaseException as exc:
    state = {{"state": "R0_FAILED", "error": f"{{type(exc).__name__}}: {{exc}}", "traceback": traceback.format_exc(), "host_control_cpuset": workload["host_control_cpuset"]}}
    raise
finally:
    try:
        cleanup = bundle.get("campaign_cleanup")
        if callable(cleanup):
            state["cleanup"] = cleanup()
    finally:
        state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\\n", encoding="utf-8")
'''
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
