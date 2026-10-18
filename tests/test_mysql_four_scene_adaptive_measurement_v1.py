from __future__ import annotations

from scripts import mysql_four_scene_adaptive_measurement_v1 as adaptive


def worker(scene: str = "hybench_xp_cpu") -> dict[str, object]:
    return {
        "worker_id": "xp_cpu_w1",
        "scene": scene,
        "cpu_quota_cores": 2.0,
        "memory_bytes": 1_000_000_000,
        "io_read_bps": 100_000_000,
        "io_write_bps": 100_000_000,
    }


def rows(*, seconds: int = 600, unstable_cpu: bool = False) -> list[dict[str, object]]:
    result = []
    cpu_ns = 0.0
    read_bytes = 0.0
    for second in range(seconds + 1):
        if second:
            window = ((second - 1) // 30) % 2
            cpu_fraction = (0.2 if window == 0 else 0.8) if unstable_cpu else 0.5
            cpu_ns += cpu_fraction * 2.0 * 1e9
            read_bytes += (0.1 if second % 2 else 0.9) * 100_000_000
        result.append({
            "captured_monotonic": 1_000.0 + second,
            "cpu_ns": int(cpu_ns),
            "memory_working_set_bytes": 400_000_000,
            "read_bytes": int(read_bytes),
            "write_bytes": 0,
        })
    return result


def test_target_only_stability_ignores_non_target_io_noise() -> None:
    evidence = adaptive.evaluate_target_resource_stability(
        rows(), worker(), 1_000.0, 300, "cpu",
    )
    assert evidence["stable"] is True
    assert evidence["qps_participates_in_stability"] is False
    assert len(evidence["windows"]) == 6


def test_unstable_target_fails_at_checkpoint() -> None:
    evidence = adaptive.evaluate_target_resource_stability(
        rows(unstable_cpu=True), worker(), 1_000.0, 300, "cpu",
    )
    assert evidence["coverage_passed"] is True
    assert evidence["variability_passed"] is False
    assert evidence["stable"] is False


def test_insufficient_sample_coverage_fails_closed() -> None:
    evidence = adaptive.evaluate_target_resource_stability(
        rows(seconds=200), worker(), 1_000.0, 300, "cpu",
    )
    assert evidence["coverage_passed"] is False
    assert evidence["stable"] is False


def test_full_suite_uses_completion_checkpoint_and_900_second_cap() -> None:
    suite_worker = worker("job_memory")
    suite_worker["worker_id"] = "job_w1"
    evidence = adaptive.completed_suite_stability(
        rows(), suite_worker, 1_000.0, 1_317.0,
    )
    assert evidence["checkpoint_seconds_diagnostic"] == 300
    assert evidence["stable"] is True
    assert evidence["minimum_measurement_required"] is False
    assert evidence["target_resource_stability_required"] is False
    assert evidence["coverage_complete_is_separate_required_gate"] is True
    assert evidence["suite_hard_timeout_seconds"] == 900


def test_full_suite_before_five_minutes_is_valid_without_minimum() -> None:
    suite_worker = worker("tpch_sf4_memory")
    suite_worker["worker_id"] = "tpch_w1"
    evidence = adaptive.completed_suite_stability(
        rows(), suite_worker, 1_000.0, 1_299.0,
    )
    assert evidence["stable"] is True
    assert evidence["minimum_measurement_required"] is False
    assert evidence["reason"] == "full_suite_completed_within_hard_timeout"


def test_full_suite_over_900_seconds_fails_hard_timeout() -> None:
    suite_worker = worker("job_memory")
    suite_worker["worker_id"] = "job_w1"
    evidence = adaptive.completed_suite_stability(
        rows(), suite_worker, 1_000.0, 1_900.001,
    )
    assert evidence["stable"] is False
    assert evidence["within_hard_timeout"] is False
    assert evidence["reason"] == "full_suite_exceeded_hard_timeout"
