#!/usr/bin/env python3
"""Publish the IQ1-IQ7 workload authority and v7 four-scene envelope."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
V6 = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260809_v6"
    / "four_scene_effective_resource_successor.json"
)
FAILED_FORMAL = (
    ROOT / "history/mysql_four_scene_20260809"
    / "four_scene_algorithm_c_formal_v1_20260809T185217p0800/run_state.json"
)
FAILED_FORMAL_ROOT = FAILED_FORMAL.parent
JOB_TPCH_WORKLOAD = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260809_v1"
    / "tpch_sf4_workload_successor.json"
)
IQ7_MANIFEST = (
    ROOT / "artifacts/mysql_xp_native_iq1_iq7_successor_v1"
    / "ce19e08b30080e182a436b4b14a0050c42f928260852ad5e2a196b05afee47f1"
    / "manifest.json"
)
IQ7_JAR = IQ7_MANIFEST.parent / "runtime/HyBench-native-IQ1-IQ7.jar"
WORKLOAD_OUTPUT = (
    ROOT / "config/formal/frozen_mysql_four_scene_native_xp_iq1_iq7_20260809_v1"
    / "workload_successor.json"
)
RESOURCE_OUTPUT = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260809_v7"
    / "four_scene_effective_resource_successor.json"
)
JOB_RESULTS = [
    FAILED_FORMAL_ROOT / f"formal_baseline/pair_{pair}/job/adapter_result.json"
    for pair in (1, 2)
]
JOB_PLAN_REPRESENTATIVES = [
    "33a.sql", "26a.sql", "1a.sql", "12a.sql",
    "5a.sql", "16a.sql", "9a.sql", "20a.sql",
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit(f"JSON object required: {path}")
    return value


def write_frozen(path: Path, value: dict) -> None:
    if path.exists():
        raise SystemExit(f"refusing to replace frozen successor: {path}")
    path.parent.mkdir(parents=True, exist_ok=False)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    path.with_name(path.name + ".sha256").write_text(
        f"{sha256(path)}  {path.name}\n", encoding="ascii"
    )


def main() -> int:
    v6 = load(V6)
    failed = load(FAILED_FORMAL)
    iq7 = load(IQ7_MANIFEST)
    job_results = [load(path) for path in JOB_RESULTS]
    if (
        v6.get("schema_version")
        != "mysql_disk2_four_scene_two_worker_effective_resource_successor_v6"
        or failed.get("status") != "FAILED_CLOSED"
        or failed.get("stage") != "FAILED"
        or any((failed.get("accepted_rounds") or {}).values())
        or iq7.get("schema_version") != "mysql_xp_native_iq1_iq7_successor_v1"
        or iq7.get("runtime_jar_sha256") != sha256(IQ7_JAR)
        or iq7.get("workload_semantics", {}).get("iq_selection")
        != "uniform_random_IQ1_IQ7"
        or iq7.get("workload_semantics", {}).get("exclusive_random_range") != [1, 8]
        or iq7.get("workload_semantics", {}).get("at_mix_percent")
        != [35, 25, 15, 15, 7, 3]
        or iq7.get("workload_semantics", {}).get("xap_xtp_reduced_ratio") != [1, 1]
        or any(result.get("query_count") != 113 for result in job_results)
        or any(result.get("query_coverage", {}).get("coverage_complete") is not True
               for result in job_results)
        or any(float(result.get("wall_time_s", 0)) <= 900.0 for result in job_results)
    ):
        raise SystemExit("v7 predecessor or IQ1-IQ7 authority differs")

    workload = {
        "schema_version": "mysql_four_scene_native_xp_iq1_iq7_workload_successor_v1",
        "status": "FROZEN",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "authority": "user_supplied_AGENTS_2026-08-09",
        "execution_authorized": True,
        "active_scenes": list(v6["active_scenes"]),
        "job_tpch_predecessor": {
            "path": str(JOB_TPCH_WORKLOAD.relative_to(ROOT)),
            "sha256": sha256(JOB_TPCH_WORKLOAD),
            "unchanged": True,
        },
        "native_hybench_xp": {
            **iq7["workload_semantics"],
            "cpu_and_io_workload_identical": True,
            "runtime_manifest": {
                "path": str(IQ7_MANIFEST.relative_to(ROOT)),
                "sha256": sha256(IQ7_MANIFEST),
            },
            "runtime_jar": {
                "path": str(IQ7_JAR.relative_to(ROOT)),
                "sha256": sha256(IQ7_JAR),
            },
        },
        "iq1_iq6_artifacts_prohibited": True,
        "fresh_xp_data_plane_and_r0_required": True,
    }
    write_frozen(WORKLOAD_OUTPUT, workload)

    successor = dict(v6)
    successor.update({
        "schema_version": "mysql_disk2_four_scene_two_worker_effective_resource_successor_v7",
        "status": "FROZEN_RUNTIME_READBACK_PENDING",
        "predecessor": {
            "path": str(V6.relative_to(ROOT)),
            "sha256": sha256(V6),
        },
        "successor_reason": (
            "failed_formal_baseline_job_wall_over_900_seconds_plus_"
            "authoritative_native_iq1_iq7_and_post_measurement_plan_capture"
        ),
        "predecessor_formal_failure": {
            "path": str(FAILED_FORMAL.relative_to(ROOT)),
            "sha256": sha256(FAILED_FORMAL),
            "status": failed["status"],
            "error": failed["error"],
            "accepted_rounds_credit": 0,
            "job_workload_results": [
                {
                    "path": str(path.relative_to(ROOT)),
                    "sha256": sha256(path),
                    "wall_time_seconds": result["wall_time_s"],
                    "query_count": result["query_count"],
                    "coverage_complete": result["query_coverage"]["coverage_complete"],
                }
                for path, result in zip(JOB_RESULTS, job_results)
            ],
        },
        "native_xp_contract": {
            "ap_selection": "IQ1-IQ7_uniform_random",
            "exclusive_random_range": [1, 8],
            "at1_at6_percent": "35/25/15/15/7/3",
            "xap_xtp_ratio": "1:1",
            "cpu_and_io_workload_identical": True,
            "workload_mix_override_prohibited": True,
            "workload_freeze": {
                "path": str(WORKLOAD_OUTPUT.relative_to(ROOT)),
                "sha256": sha256(WORKLOAD_OUTPUT),
            },
        },
        "plan_capture_contract": {
            "outside_formal_measurement_wall_time": True,
            "job_full_workload_query_count": 113,
            "job_representative_queries": JOB_PLAN_REPRESENTATIVES,
            "tpch_representative_queries": ["Q6", "Q11"],
            "plan_capture_failure_is_fail_closed": True,
        },
        "formal_launch_authorized": False,
        "launch_performed": False,
        "accepted_rounds": {scene: 0 for scene in v6["active_scenes"]},
    })
    workers = []
    for original in v6["workers"]:
        worker = dict(original)
        if worker["scene"] == "job_memory":
            worker["v6_cpu_quota_cores"] = original["cpu_quota_cores"]
            worker["cpu_quota_cores"] = 4.0
        workers.append(worker)
    successor["workers"] = workers
    write_frozen(RESOURCE_OUTPUT, successor)
    print(WORKLOAD_OUTPUT)
    print(RESOURCE_OUTPUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
