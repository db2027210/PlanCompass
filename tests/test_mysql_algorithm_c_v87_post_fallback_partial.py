from __future__ import annotations


def test_v87_accepts_only_exact_post_fallback_boundary() -> None:
    from scripts import build_mysql_algorithm_c_outer_checkpoint_v4 as checkpointmod
    from scripts import run_mysql_algorithm_c_async_scene_v87 as v87

    checkpoint = checkpointmod.verify_checkpoint(
        v87.ROOT
        / "history/mysql_four_scene_20260809/"
        "v992_to_c_cpu_outer17_docker_timeout_checkpoint_20260828/"
        "prepared_binding.json"
    )
    reconstructed = checkpoint["reconstructed_state"]
    result = v87.validate_post_fallback_partial(
        source_root=v87.SOURCE_ROOT,
        checkpoint=checkpoint,
        reconstructed=reconstructed,
        start_rank=5,
    )
    assert result is not None
    assert result["outer_attempt"] == 18
    assert result["start_rank"] == 6
    assert len(result["evaluations"][v87.SCENE]) == 3
    assert result["completed_fallback_reused_by_v86"] is True
    assert result["new_physical_work_starts_at"] == "current_interval_plan_coverage"
    assert result["accepted_state_credit_before_continuation"] == 0
