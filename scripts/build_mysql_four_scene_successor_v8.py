#!/usr/bin/env python3
"""Publish v8 with the executable IQ1-IQ7-v2 runtime; resources unchanged."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
V7 = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260809_v7"
    / "four_scene_effective_resource_successor.json"
)
FAILED_R0 = (
    ROOT / "history/mysql_four_scene_20260809"
    / "four_scene_native_iq1_iq7_r0_v7_retry2_20260809T195600p0800/r0_binding.json"
)
JOB_TPCH_WORKLOAD = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260809_v1"
    / "tpch_sf4_workload_successor.json"
)
RUNTIME_MANIFEST = (
    ROOT / "artifacts/mysql_xp_native_iq1_iq7_successor_v2"
    / "e3e3a32e6b095e10d5e229be5ec211131f626374bc82764d98ad3f910344e017"
    / "manifest.json"
)
RUNTIME_JAR = RUNTIME_MANIFEST.parent / "runtime/HyBench-native-IQ1-IQ7-v2.jar"
WORKLOAD_OUTPUT = (
    ROOT / "config/formal/frozen_mysql_four_scene_native_xp_iq1_iq7_20260809_v2"
    / "workload_successor.json"
)
RESOURCE_OUTPUT = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260809_v8"
    / "four_scene_effective_resource_successor.json"
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit(f"JSON object required: {path}")
    return value


def write(path: Path, value: dict) -> None:
    if path.exists():
        raise SystemExit(f"refusing to replace frozen output: {path}")
    path.parent.mkdir(parents=True, exist_ok=False)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii"
    )


def main() -> int:
    v7 = load(V7)
    failed = load(FAILED_R0)
    runtime = load(RUNTIME_MANIFEST)
    if (
        v7.get("schema_version")
        != "mysql_disk2_four_scene_two_worker_effective_resource_successor_v7"
        or failed.get("status") != "R0_FAILED_CLOSED"
        or len(failed.get("failures") or []) != 4
        or any((failed.get("accepted_rounds") or {}).values())
        or runtime.get("status") != "EXECUTION_AUTHORIZED"
        or runtime.get("execution_authorized") is not True
        or runtime.get("runtime_jar_sha256") != sha(RUNTIME_JAR)
        or runtime.get("workload_semantics", {}).get("iq_selection")
        != "uniform_random_IQ1_IQ7"
        or runtime.get("workload_semantics", {}).get("exclusive_random_range") != [1, 8]
        or runtime.get("workload_semantics", {}).get("at_mix_percent")
        != [35, 25, 15, 15, 7, 3]
    ):
        raise SystemExit("v8 predecessor/runtime authority differs")
    workload = {
        "schema_version": "mysql_four_scene_native_xp_iq1_iq7_workload_successor_v2",
        "status": "FROZEN",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "authority": "user_supplied_AGENTS_2026-08-09",
        "execution_authorized": True,
        "active_scenes": list(v7["active_scenes"]),
        "job_tpch_predecessor": {
            "path": str(JOB_TPCH_WORKLOAD.relative_to(ROOT)),
            "sha256": sha(JOB_TPCH_WORKLOAD),
            "unchanged": True,
        },
        "native_hybench_xp": {
            **runtime["workload_semantics"],
            "cpu_and_io_workload_identical": True,
            "runtime_manifest": {
                "path": str(RUNTIME_MANIFEST.relative_to(ROOT)),
                "sha256": sha(RUNTIME_MANIFEST),
            },
            "runtime_jar": {
                "path": str(RUNTIME_JAR.relative_to(ROOT)),
                "sha256": sha(RUNTIME_JAR),
            },
        },
        "predecessor_failed_r0": {
            "path": str(FAILED_R0.relative_to(ROOT)),
            "sha256": sha(FAILED_R0),
            "accepted_rounds_credit": 0,
        },
        "iq1_iq6_artifacts_prohibited": True,
        "incomplete_iq1_iq7_v1_artifacts_prohibited": True,
        "fresh_xp_data_plane_and_r0_required": True,
    }
    write(WORKLOAD_OUTPUT, workload)
    successor = dict(v7)
    successor.update({
        "schema_version": "mysql_disk2_four_scene_two_worker_effective_resource_successor_v8",
        "status": "FROZEN_RUNTIME_READBACK_PENDING",
        "predecessor": {"path": str(V7.relative_to(ROOT)), "sha256": sha(V7)},
        "successor_reason": "complete_missing_native_IQ7_execution_path",
        "resource_envelope_unchanged_from_v7": True,
        "predecessor_r0_failure": {
            "path": str(FAILED_R0.relative_to(ROOT)),
            "sha256": sha(FAILED_R0),
            "failure_causes": ["all_four_XP_workers_missing_execIQ7"],
            "job_wall_time_seconds": [
                failed["results"]["job_tpch_pair_1"]["workloads"]["job"]["wall_time_s"],
                failed["results"]["job_tpch_pair_2"]["workloads"]["job"]["wall_time_s"],
            ],
            "job_tpch_formal_measurements_valid": True,
            "target_bottleneck_gate_passed": failed["target_bottleneck_gate"]["passed"],
            "accepted_rounds_credit": 0,
        },
        "native_xp_contract": {
            "ap_selection": "IQ1-IQ7_uniform_random",
            "exclusive_random_range": [1, 8],
            "iq7_sql_authority": "existing_HyBench_AP_7",
            "iq7_parameter_authority": "existing_HyBench_execQ7",
            "at1_at6_percent": "35/25/15/15/7/3",
            "xap_xtp_ratio": "1:1",
            "cpu_and_io_workload_identical": True,
            "workload_mix_override_prohibited": True,
            "workload_freeze": {
                "path": str(WORKLOAD_OUTPUT.relative_to(ROOT)),
                "sha256": sha(WORKLOAD_OUTPUT),
            },
        },
        "formal_launch_authorized": False,
        "launch_performed": False,
        "accepted_rounds": {scene: 0 for scene in v7["active_scenes"]},
    })
    if successor["workers"] != v7["workers"]:
        raise SystemExit("v8 resource envelope unexpectedly changed")
    write(RESOURCE_OUTPUT, successor)
    print(WORKLOAD_OUTPUT)
    print(RESOURCE_OUTPUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
