from pathlib import Path

from scripts import mysql_async_xp_post_reset_settle_v1 as settle
from scripts import (
    run_mysql_four_scene_algorithm_b_dynamic_support_resume_core_v27 as core,
)
from scripts import run_mysql_algorithm_b_async_scene_v36 as async_b


ROOT = Path(__file__).resolve().parents[1]
FAILED_XP_IO = (
    ROOT / "history/mysql_four_scene_20260809"
    / "v36_formal_async_outer_20260819_attempt044_retry009_xp_io"
)


def _early_row() -> dict:
    return {
        "scene": "tpch_sf4_memory",
        "rank": 1,
        "params": {"row_evaluate_cost": 0.08},
        "hard_gate_passed": False,
        "coverage_complete": True,
        "hard_gate_checks": {
            "client_qps_audit_positive": True,
            "coverage_complete": True,
            "database_qps_positive": True,
            "formal_measurement_valid": True,
            "mysql_server_liveness": True,
            "resource_sample_coverage_sufficient": True,
            "resource_utilization_finite": True,
            "restart_and_oom_gate_passed": True,
            "target_resource_stability_passed": False,
            "three_level_fingerprint_complete": True,
        },
        "resource_stability": {
            "stable": False,
            "elapsed_seconds": 139.5,
            "reason": "full_suite_completed_before_minimum_measurement_boundary",
        },
        "workload_result": {
            "independent_wall_time_s": 139.4,
            "query_coverage": {
                "coverage_complete": True,
                "missing_queries": [],
            },
        },
    }


def test_complete_fast_suite_is_candidate_local_failure() -> None:
    row = _early_row()
    batch = {
        "status": "FAILED_CLOSED",
        "failures": [],
        "measurements": {"tpch_w1": row},
    }
    assert core.minimum_boundary_candidate_rows(batch) == {"tpch_w1": row}


def test_other_hard_gate_failure_remains_fail_closed() -> None:
    row = _early_row()
    row["hard_gate_checks"]["coverage_complete"] = False
    batch = {
        "status": "FAILED_CLOSED",
        "failures": [],
        "measurements": {"tpch_w1": row},
    }
    assert core.minimum_boundary_candidate_rows(batch) == {}


def test_blkio_write_counters_are_device_and_operation_scoped(tmp_path: Path) -> None:
    cgroup = tmp_path / "worker"
    cgroup.mkdir()
    (cgroup / "blkio.throttle.io_service_bytes_recursive").write_text(
        "259:6 Read 100\n259:6 Write 8192\n259:6 Total 8292\nTotal 8292\n",
        encoding="ascii",
    )
    (cgroup / "blkio.throttle.io_serviced_recursive").write_text(
        "259:6 Read 2\n259:6 Write 3\n259:6 Total 5\nTotal 5\n",
        encoding="ascii",
    )
    assert settle._io_totals({"xp_io_w1": cgroup}, "259:6") == (8192, 3)


def test_actual_xp_drain_failure_is_candidate_local_technical() -> None:
    batch_path = FAILED_XP_IO / "outer_007/top5_batch_1/batch_result.json"
    technical = core.technical_candidate_failure_batch(
        core.alg.load(batch_path), batch_path=batch_path,
    )
    assert technical is not None
    assert technical["classification"] == "TECHNICAL_CANDIDATE_TERMINAL_EXCLUDED"
    assert technical["failed_worker_ids"] == ["xp_io_w1"]
    assert technical["candidate_state_mutation"] is False


def test_actual_xp_drain_failure_has_completed_outer_resume_boundary() -> None:
    evidence = async_b.validate_completed_scene_technical_resume(FAILED_XP_IO)
    assert evidence["completed_outer_attempt"] == 6
    assert evidence["interrupted_outer_attempt"] == 7
    assert evidence["interrupted_outer_measurement_credit"] is False
    assert set(evidence["completed_outer_evaluations"]) == set(range(1, 7))
    assert evidence["technical_failure"]["failed_worker_ids"] == ["xp_io_w1"]
