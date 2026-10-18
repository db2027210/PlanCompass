#!/usr/bin/env python3
"""Publish the fail-closed 2026-08-06 MySQL three-workload freeze."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "config/formal/frozen_mysql_three_workloads_20260806_v1"
JOB_DIR = Path("/home/anonymous/dataset/pg_data/join-order-benchmark")
XP_BUNDLE_ID = "ce19e08b30080e182a436b4b14a0050c42f928260852ad5e2a196b05afee47f1"
XP_BUNDLE = ROOT / "artifacts/mysql_xp_native_iq1_iq7_successor_v1" / XP_BUNDLE_ID
OLD_PARAMETERS = ROOT / "config/formal/frozen_mysql_workloads_and_parameters_20260723_v3/effective_parameter_spaces.json"
LANES = [[9], [22, 16, 12, 8, 15], [17, 6, 20, 4, 3],
         [19, 18, 13, 10, 21], [2, 11, 14, 5, 7, 1]]
JOB_TEMPLATE = Path("/disk1/ra_mysql_isolated_v2/job-base")
TPCH_TEMPLATE = Path("/disk2/ra_mysql_isolated_v2/tpch-memory")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def encoded(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def main() -> int:
    queries = sorted(
        (path for path in JOB_DIR.glob("*.sql") if re.fullmatch(r"\d+[a-z]\.sql", path.name)),
        key=lambda path: (int(re.match(r"\d+", path.name).group()), path.name),
    )
    if len(queries) != 113:
        raise SystemExit(f"canonical JOB coverage must be 113, got {len(queries)}")
    if sorted(q for lane in LANES for q in lane) != list(range(1, 23)):
        raise SystemExit("TPC-H lanes do not cover Q1-Q22 exactly once")
    xp_manifest = json.loads((XP_BUNDLE / "manifest.json").read_text())
    if (
        xp_manifest.get("bundle_id") != XP_BUNDLE_ID
        or xp_manifest.get("workload_semantics", {}).get("exclusive_random_range") != [1, 8]
    ):
        raise SystemExit("native XP successor is not the IQ1-IQ7 authority")
    old_parameters = json.loads(OLD_PARAMETERS.read_text())
    active = old_parameters["benchmark_candidate_parameters"]
    workloads = {
        "schema_version": "mysql_three_workload_freeze_v1",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "active_benchmarks": ["job", "tpch", "hybench_xp"],
        "retired": {
            "tpcc": "immutable_historical_only_no_new_launch_or_aggregate",
            "xp_io_ap13_fixed_q9": "immutable_historical_only_no_new_launch_or_aggregate",
            "partial_job_or_tpch": "not_formal_measurement",
        },
        "job": {
            "database": "imdb_job",
            "query_directory": str(JOB_DIR),
            "queries": [{"name": p.name, "sha256": sha(p)} for p in queries],
            "required_count": 113,
            "executions_per_measurement": 1,
            "workers": 16,
            "long_query_threshold_seconds": 30,
            "maximum_concurrent_long_queries": 4,
            "scheduling": "shortest_first_with_bounded_long_query_semaphore",
        },
        "tpch": {
            "database": "tpch",
            "scale_factor": 10,
            "queries": [f"Q{i}" for i in range(1, 23)],
            "executions_per_measurement": 1,
            "lanes": LANES,
            "lane_count": 5,
            "terminal_per_lane": 1,
            "lane_order": "short_first_historical_duration_balanced",
        },
        "job_tpch_joint_measurement": {
            "outer_start": "concurrent",
            "minimum_target_wall_seconds": 600,
            "maximum_valid_wall_seconds": 900,
            "missing_any_sql_action": "FAIL_CLOSED",
            "over_900_seconds_action": "FAIL_CLOSED",
        },
        "hybench_xp": {
            "mode": "native_runxp",
            "scale_factor": "10x",
            "iq_queries": [f"IQ{i}" for i in range(1, 8)],
            "selection": "uniform_random",
            "at_mix_percent": {"AT1": 35, "AT2": 25, "AT3": 15,
                               "AT4": 15, "AT5": 7, "AT6": 3},
            "baseline_clients": {"xapclient": 8, "xtpclient": 8},
            "reduced_client_ratio": {"xap": 1, "xtp": 1},
            "allowed_scaling": "positive_integer_common_factor_only",
            "forbidden": ["ap_query_ids", "ap_query_weights", "ap_query_sequence",
                          "ap_query_sequence_limit", "AT/TP mix overrides",
                          "one-query mode", "SQL rewrite"],
            "runtime_bundle_id": XP_BUNDLE_ID,
            "runtime_manifest_sha256": sha(XP_BUNDLE / "manifest.json"),
            "runtime_jar_sha256": xp_manifest["runtime_jar_sha256"],
        },
        "parameter_space_derivation": {
            "historical_input_only": str(OLD_PARAMETERS),
            "historical_input_sha256": sha(OLD_PARAMETERS),
            "active_parameters": {name: active[name] for name in ("job", "tpch", "hybench_xp")},
            "nonempty_subset_counts": {"job": 15, "tpch": 15, "hybench_xp": 127},
        },
        "execution_authorized": False,
        "status": "SEALED_WORKLOAD_NOT_LAUNCH_AUTHORITY",
    }
    envelope = {
        "schema_version": "mysql_xp_cpu_resource_envelope_freeze_v1",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "benchmark": "hybench_xp",
        "target_bottleneck": "cpu",
        "pressure_construction": "resource_envelope_only",
        "intended": {
            "cpu_period_us": 100000, "cpu_quota_us": 276820,
            "cpu_quota_cores": 2.7682, "cpuset_cpus": "184-199,440-455",
            "cpuset_mems": "1", "memory_bytes": 8589934592,
            "memory_swap_bytes": 0, "io_device": "8:0",
            "io_read_bps": 1205862400, "io_write_bps": 1205862400,
            "pids_max": 1024, "innodb_buffer_pool_bytes": 4294967296,
        },
        "required_effective_readback": [
            "inner_and_outer_cpu_quota", "cpuset_cpus", "cpuset_mems",
            "memory", "swap", "io_device_and_caps", "pids",
            "innodb_buffer_pool", "cpu_and_numa_isolation",
            "co_resident_inventory", "rolling_dynamic_non_interference",
        ],
        "workload_freeze_sha256": None,
        "execution_authorized": False,
        "status": "FROZEN_INTENT_READBACK_REQUIRED",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    workload_path = OUT / "workload_freeze.json"
    workload_path.write_bytes(encoded(workloads))
    envelope["workload_freeze_sha256"] = sha(workload_path)
    envelope_path = OUT / "xp_cpu_resource_envelope.json"
    envelope_path.write_bytes(encoded(envelope))
    joint_envelope = {
        "schema_version": "mysql_full_job_tpch_joint_resource_envelope_freeze_v1",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "execution": "concurrent_separate_mysql_instances",
        "shared_instance_prohibited": True,
        "workload_freeze_sha256": sha(workload_path),
        "job": {
            "template_path": str(JOB_TEMPLATE),
            "template_exists_at_freeze": JOB_TEMPLATE.is_dir(),
            "storage_mount": "/disk1", "storage_device": "/dev/nvme1n1",
            "storage_device_major_minor": "259:5", "host_port": 65211,
            "intended": {
                "cpu_period_us": 100000, "cpu_quota_us": 300000,
                "cpu_quota_cores": 3.0, "cpuset_cpus": "176-183,432-439",
                "cpuset_mems": "1", "memory_bytes": 6442450944,
                "memory_swap_bytes": 0, "io_read_bps": 1205862400,
                "io_write_bps": 1205862400, "pids_max": 512,
                "innodb_buffer_pool_bytes": 4294967296,
            },
        },
        "tpch": {
            "template_path": str(TPCH_TEMPLATE),
            "template_exists_at_freeze": TPCH_TEMPLATE.is_dir(),
            "storage_mount": "/disk2", "storage_device": "/dev/nvme2n1",
            "storage_device_major_minor": "259:6", "host_port": 65212,
            "intended": {
                "cpu_period_us": 100000, "cpu_quota_us": 750000,
                "cpu_quota_cores": 7.5, "cpuset_cpus": "200-207,456-463",
                "cpuset_mems": "1", "memory_bytes": 5583454208,
                "memory_swap_bytes": 0, "io_read_bps": 872415232,
                "io_write_bps": 872415232, "pids_max": 512,
                "innodb_buffer_pool_bytes": 4294967296,
            },
        },
        "required_effective_readback_per_instance": [
            "container_and_proc_identity", "cpu_period_and_quota", "cpuset_cpus",
            "cpuset_mems", "memory", "swap", "io_device_and_caps", "pids",
            "innodb_buffer_pool", "session_owned_full_physical_copy",
            "database_schema_and_row_count_attestation",
        ],
        "joint_required_gates": [
            "disjoint_cpuset", "disjoint_storage_device", "disjoint_container",
            "disjoint_host_port", "co_resident_inventory",
            "rolling_dynamic_non_interference", "full_113_and_q1_q22_coverage",
        ],
        "known_prelaunch_condition": (
            "retire_or_relocate_idle_mysql_job_preflight_before_claiming_job_cpuset"
        ),
        "execution_authorized": False,
        "status": "FROZEN_INTENT_READBACK_REQUIRED",
    }
    joint_path = OUT / "job_tpch_resource_envelope.json"
    joint_path.write_bytes(encoded(joint_envelope))
    manifest = {
        "schema_version": "mysql_three_workload_formal_freeze_manifest_v1",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "artifacts": {
            "workload_freeze.json": sha(workload_path),
            "xp_cpu_resource_envelope.json": sha(envelope_path),
            "job_tpch_resource_envelope.json": sha(joint_path),
            "mysql_workload_adapter.py": sha(ROOT / "mysql_workload_adapter.py"),
            "mysql_benchmark_command.py": sha(ROOT / "mysql_benchmark_command.py"),
            "xp_runtime_manifest.json": sha(XP_BUNDLE / "manifest.json"),
        },
        "formal_launch_authorized": False,
        "launch_performed": False,
        "accepted_rounds": {"job": 0, "tpch": 0, "hybench_xp": 0},
        "status": "SEALED_PRELAUNCH_GATES_INCOMPLETE",
    }
    manifest_path = OUT / "manifest.json"
    manifest_path.write_bytes(encoded(manifest))
    (OUT / "manifest.json.sha256").write_text(f"{sha(manifest_path)}  manifest.json\n")
    (OUT / "checksums.sha256").write_text(
        "".join(f"{sha(OUT / name)}  {name}\n" for name in
                ("workload_freeze.json", "xp_cpu_resource_envelope.json",
                 "job_tpch_resource_envelope.json", "manifest.json"))
    )
    print(OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
