#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from system_resource_monitor import parse_hybench_benchmark_output
from binary_plan_resource_tuner import (
    detect_hybench_technical_failures,
    workload_completeness_audit,
)


def main() -> None:
    marker = {
        "benchmark": {"throughput_qps": 321.5},
        "benchmark_latency": {
            "all_p90_avg": 12.0,
            "all_p95_avg": 15.0,
            "all_p99_avg": 21.0,
        },
    }
    parsed = parse_hybench_benchmark_output(
        ["noise", "RESOURCE_ADAPTIVE_BENCHMARK_JSON=" + json.dumps(marker)]
    )
    assert parsed["benchmark"]["throughput_qps"] == 321.5
    assert parsed["benchmark"]["latency_p95"] == "15.0"
    assert parsed["benchmark_latency"]["all_p99_avg"] == 21.0

    job = parse_hybench_benchmark_output(
        [
            "JOB round=1 query=1/113 file=1a.sql elapsed_ms=10.000 returncode=0",
            "JOB round=1 query=2/113 file=1b.sql elapsed_ms=20.000 returncode=0",
            "JOB round=1 query=3/113 file=1c.sql elapsed_ms=30.000 returncode=0",
            "JOB round=1 query=4/113 file=2a.sql elapsed_ms=999.000 returncode=3",
        ]
    )
    assert job["benchmark"]["query_count"] == 3
    assert job["benchmark_latency"]["all_p90_avg"] == 28.0
    assert job["benchmark_latency"]["all_p95_avg"] == 29.0
    assert abs(job["benchmark_latency"]["all_p99_avg"] - 29.8) < 1e-9
    assert abs(float(job["benchmark"]["latency_p99"]) - 29.8) < 1e-9

    xp_fixed_horizon = parse_hybench_benchmark_output(
        [
            "Analytical Transaction AT1 : max rt : 200.0 | min rt :10.0 "
            "| avg rt : 50.0 | 90% rt : 100.0 | 95% rt : 150.0 "
            "| 99% rt : 190.0",
            "Current 2/10 time TP TPS is 0.30",
        ]
    )
    assert xp_fixed_horizon["benchmark"]["tp_tps"] == "0.30"
    assert xp_fixed_horizon["benchmark_latency"]["xp_at_p95_avg"] == 150.0
    assert xp_fixed_horizon["benchmark_latency"]["all_p99_avg"] == 190.0
    assert xp_fixed_horizon["benchmark"]["latency_p95"] == "150.0"

    hidden_npe = detect_hybench_technical_failures(
        [
            "XP Workload is done",
            "java.lang.NullPointerException: Related_Blocked_Transfer_ids is null",
        ]
    )
    assert [item["type"] for item in hidden_npe] == ["java_null_pointer_exception"]
    assert detect_hybench_technical_failures(["XP Workload is done"]) == []

    job_counts = {f"q{index}": 1 for index in range(1, 114)}
    job_completeness = workload_completeness_audit(
        {"binary_plan_tuning": {"workload_name": "job"}},
        {
            "query_count": 113,
            "query_counts": job_counts,
            "all_ap_queries_completed": True,
            "exactly_once": True,
        },
        [],
        {},
    )
    assert job_completeness["passed"] is True
    slow_job = workload_completeness_audit(
        {"binary_plan_tuning": {"workload_name": "job"}},
        {
            "query_count": 113,
            "query_counts": job_counts,
            "all_ap_queries_completed": True,
            "exactly_once": True,
            "target_wall_time_s": 900.0,
            "wall_time_s": 901.0,
            "completed_within_target_wall_time": False,
        },
        [],
        {},
    )
    assert slow_job["passed"] is False
    assert "job_target_wall_time_exceeded" in slow_job["errors"]

    tpch_sequence = [f"Q{index}" for index in range(1, 23)]
    tpch_completeness = workload_completeness_audit(
        {"binary_plan_tuning": {"workload_name": "tpch"}},
        {
            "query_count": 22,
            "query_sequence": tpch_sequence,
            "query_counts": {query_name: 1 for query_name in tpch_sequence},
            "all_ap_queries_completed": True,
            "exactly_once": True,
        },
        [],
        {},
    )
    assert tpch_completeness["passed"] is True
    slow_tpch = workload_completeness_audit(
        {"binary_plan_tuning": {"workload_name": "tpch"}},
        {
            "query_count": 22,
            "query_sequence": tpch_sequence,
            "query_counts": {query_name: 1 for query_name in tpch_sequence},
            "all_ap_queries_completed": True,
            "exactly_once": True,
            "target_wall_time_s": 900.0,
            "wall_time_s": 901.0,
            "completed_within_target_wall_time": False,
        },
        [],
        {},
    )
    assert slow_tpch["passed"] is False
    assert "tpch_target_wall_time_exceeded" in slow_tpch["errors"]
    incomplete_tpch = dict(tpch_completeness)
    assert incomplete_tpch["passed"] is True
    bad_tpch = workload_completeness_audit(
        {"binary_plan_tuning": {"workload_name": "tpch"}},
        {
            "query_count": 22,
            "query_sequence": list(reversed(tpch_sequence)),
            "query_counts": {query_name: 1 for query_name in tpch_sequence},
            "all_ap_queries_completed": True,
            "exactly_once": True,
        },
        [],
        {},
    )
    assert bad_tpch["passed"] is False

    tpch_parallel = workload_completeness_audit(
        {
            "binary_plan_tuning": {
                "workload_name": "tpch",
                "validation": {"hybench_xp": {"tpclient": 8}},
            }
        },
        {
            "execution_mode": "native_tpch_parallel_lpt_q1_q22_exactly_once",
            "worker_count": 8,
            "lane_assignments": [
                ["Q18"],
                ["Q4"],
                ["Q9"],
                ["Q5", "Q2"],
                ["Q20", "Q19", "Q17", "Q11", "Q22"],
                ["Q8", "Q1", "Q21", "Q10", "Q15"],
                ["Q6", "Q12", "Q13", "Q16"],
                ["Q7", "Q14", "Q3"],
            ],
            "query_count": 22,
            "query_counts": {query_name: 1 for query_name in tpch_sequence},
            "all_ap_queries_completed": True,
            "exactly_once": True,
        },
        [],
        {},
    )
    assert tpch_parallel["passed"] is True
    assert tpch_parallel["parallel_contract"] is True

    bad_tpch_parallel = workload_completeness_audit(
        {
            "binary_plan_tuning": {
                "workload_name": "tpch",
                "validation": {"hybench_xp": {"tpclient": 8}},
            }
        },
        {
            "execution_mode": "native_tpch_parallel_lpt_q1_q22_exactly_once",
            "worker_count": 7,
            "lane_assignments": [
                ["Q18"],
                ["Q4"],
                ["Q9"],
                ["Q5", "Q2"],
                ["Q20", "Q19", "Q17", "Q11", "Q22"],
                ["Q8", "Q1", "Q21", "Q10", "Q15"],
                ["Q6", "Q12", "Q13", "Q16"],
                ["Q7", "Q14", "Q3", "Q3"],
            ],
            "query_count": 22,
            "query_counts": {
                **{query_name: 1 for query_name in tpch_sequence},
                "Q23": 1,
            },
            "all_ap_queries_completed": True,
            "exactly_once": True,
        },
        [],
        {},
    )
    assert bad_tpch_parallel["passed"] is False
    assert "tpch_parallel_worker_count_mismatch" in bad_tpch_parallel["errors"]
    assert (
        "tpch_parallel_lane_assignments_not_q1_q22_exactly_once"
        in bad_tpch_parallel["errors"]
    )
    assert "tpch_unexpected_query_names" in bad_tpch_parallel["errors"]

    xp_lines = [
        f"Interative Query {index} : max rt : 2.0 | min rt : 1.0 | avg rt : 1.5"
        for index in range(1, 7)
    ] + [
        f"Analytical Transaction AT{index} : max rt : 2.0 | min rt : 1.0 | avg rt : 1.5"
        for index in range(1, 7)
    ]
    xp_run_config = {
        "xtpclient": 32,
        "xapclient": 4,
        "at_percentages": {
            "at1_percent": 40,
            "at2_percent": 30,
            "at3_percent": 15,
            "at4_percent": 5,
            "at5_percent": 7,
            "at6_percent": 3,
        },
        "statement_file_sha256": "expected",
        "expected_statement_file_sha256": "expected",
        "native_jar_sha256": "expected-jar",
        "expected_native_jar_sha256": "expected-jar",
    }
    xp_completeness = workload_completeness_audit(
        {"binary_plan_tuning": {"workload_name": "hybench_xp"}},
        {},
        xp_lines,
        xp_run_config,
    )
    assert xp_completeness["passed"] is True
    missing_iq = workload_completeness_audit(
        {"binary_plan_tuning": {"workload_name": "hybench_xp"}},
        {},
        xp_lines[1:],
        xp_run_config,
    )
    assert missing_iq["passed"] is False
    assert "xp_iq1_iq6_not_all_observed" in missing_iq["errors"]
    print("benchmark output parser tests passed", flush=True)


if __name__ == "__main__":
    main()
