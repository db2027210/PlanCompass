#!/usr/bin/env python3
"""Fast offline checks for the shared Algorithm B/C no-bottleneck target."""

from __future__ import annotations

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from binary_plan_resource_tuner import outer_semantic_acceptance_details
from shell.run_pg_algorithm_b_scene import _utility_calculator
from shell.run_pg_algorithm_c_scene import (
    baseline_execution_domain_audit,
    formal_inner_scoring_options,
    native_xp_runtime_contract_audit,
    predicted_target_direction_audit,
)


def config() -> dict[str, object]:
    return {
        "algorithm_c": {"qps_noise_gate": {"enabled": False}},
        "binary_plan_tuning": {
            "acceptance_policy": {
                "target_overrides": {
                    "none": {
                        "resource_capacity": {
                            "cpu_cores": 4.0,
                            "memory_gib": 12.0,
                            "io_mib_s": 30.0,
                        }
                    }
                }
            },
            "binary_search": {
                "nested_loop": {
                    "outer_acceptance": {
                        "min_db_qps_gain_pct": 0.1,
                        "non_target_bottleneck_threshold_pct": 80.0,
                        "qps_reference_mode": "last_accepted",
                        "qps_threshold_mode": "static_floor",
                    }
                }
            },
        },
    }


def replay(label: str, qps: float, cpu: float) -> dict[str, object]:
    return {
        "label": label,
        "status": "ok",
        "db_qps": qps,
        "resources": {"cpu": cpu, "memory": 2.0, "io": 6.0},
        "resource_stats": {"resource_monitor_ok": True},
    }


def test_none_inner_scoring() -> None:
    options = formal_inner_scoring_options("none")
    assert options["resource_target_ranges"] == {
        resource: {
            "min": 0.30,
            "max": 0.50,
            "hard": 0.80,
            "role": (
                "non_bottleneck_io_cost"
                if resource == "io"
                else "non_bottleneck"
            ),
        }
        for resource in ("cpu", "memory", "io")
    }
    _utility_calculator("none")
    assert predicted_target_direction_audit({}, "none") == {
        "ready": True,
        "predicted_target_down": None,
        "reason": "no_target_resource_not_applicable",
        "qps_used": False,
    }


def test_none_outer_acceptance() -> None:
    baseline = replay("baseline", 100.0, 2.5)
    safe = outer_semantic_acceptance_details(
        replay("safe", 101.0, 2.6),
        baseline,
        baseline,
        target="none",
        cfg=config(),
    )
    assert safe["accepted"] is True
    assert safe["non_target_safe"] is True
    saturated = outer_semantic_acceptance_details(
        replay("saturated", 101.0, 3.2),
        baseline,
        baseline,
        target="none",
        cfg=config(),
    )
    assert saturated["accepted"] is False
    assert saturated["non_target_bottleneck_resources"] == ["cpu"]


def test_native_xp_runtime_contract_identity() -> None:
    at_mix = {
        "at1_percent": 40,
        "at2_percent": 30,
        "at3_percent": 15,
        "at4_percent": 5,
        "at5_percent": 7,
        "at6_percent": 3,
    }
    runtime = {
        "xtpclient": 1,
        "xapclient": 1,
        "xp_run_mins": 5,
        "native_jar_sha256": "jar",
        "statement_file_sha256": "sql",
        **at_mix,
    }
    cfg = {
        "algorithm_c": {
            "workload_contract": {
                "benchmark": "hybench_xp",
                "native_xp": {
                    "xtpclient": 1,
                    "xapclient": 1,
                    "xp_run_mins": 5,
                    "native_jar_sha256": "jar",
                    "statement_file_sha256": "sql",
                    "at_percentages": at_mix,
                },
            }
        },
        "binary_plan_tuning": {"validation": {"hybench_xp": runtime}},
    }
    passed = native_xp_runtime_contract_audit(cfg, target="none")
    assert passed["passed"] is True
    runtime["xtpclient"] = 2
    failed = native_xp_runtime_contract_audit(cfg, target="none")
    assert failed["passed"] is False
    assert failed["errors"] == ["native_xp_runtime_mismatch_xtpclient"]


def test_no_mutex_baseline_requires_synchronized_algorithm_cohort() -> None:
    qualification = {
        "qualification_id": "q1",
        "thresholds": {
            "explicit_user_authorized_no_mutex": True,
            "global_ssd_stable_window_enforced": False,
            "global_ssd_utilization_is_informational": True,
        },
    }
    completion = {
        "passed": True,
        "completed_count": 2,
        "repetition": 1,
        "barrier_stage_sha256": "stage",
    }
    row = {
        "label": "baseline",
        "ssd_qualification": qualification,
        "baseline_cohort_completion_barrier": completion,
    }
    cohort = {"enabled": True, "cohort_size": 2, "cohort_id_sha256": "cohort"}
    passed = baseline_execution_domain_audit(
        [row],
        {"ssd_qualification": qualification},
        expected_no_mutex=True,
        cohort=cohort,
    )
    assert passed["passed"] is True
    failed = baseline_execution_domain_audit(
        [row],
        {"ssd_qualification": qualification},
        expected_no_mutex=True,
        cohort={"enabled": False, "cohort_size": 1},
    )
    assert failed["errors"] == ["no_mutex_baseline_requires_multi_lane_cohort"]


def main() -> int:
    test_none_inner_scoring()
    test_none_outer_acceptance()
    test_native_xp_runtime_contract_identity()
    test_no_mutex_baseline_requires_synchronized_algorithm_cohort()
    print("pg_none_target_algorithms: PASS", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
