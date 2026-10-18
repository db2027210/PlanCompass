from __future__ import annotations

from pathlib import Path


def test_v89_reuses_exact_io_post_fallback_boundary() -> None:
    from scripts import build_mysql_algorithm_c_outer_checkpoint_v4 as checkpointmod
    from scripts import run_mysql_algorithm_c_async_scene_v89 as v89

    checkpoint = checkpointmod.verify_checkpoint(
        v89.ROOT / "history/mysql_four_scene_20260809/v1006_to_c_io_outer12_docker_timeout_checkpoint_20260828/prepared_binding.json"
    )
    result = v89.validate_post_fallback_partial(
        source_root=v89.SOURCE_ROOT,
        checkpoint=checkpoint,
        reconstructed=checkpoint["reconstructed_state"],
        start_rank=5,
    )
    assert result is not None
    assert result["outer_attempt"] == 13
    assert len(result["evaluations"][v89.SCENE]) == 1
    assert result["empty_candidate_ranks"] == [2, 3, 4, 5]
    baseline = v89.partial_runtime.alg.load(Path(result["baseline"]["path"]))
    assert set(baseline["measurements"]) == {"xp_io_w1", "xp_io_w2", "xp_io_w3", "xp_io_w4"}
    assert result["new_physical_work_starts_at"] == "current_interval_plan_coverage"
    assert result["accepted_state_credit_before_continuation"] == 0


def test_v89_fallback_source_is_exact() -> None:
    from scripts import run_mysql_algorithm_c_async_scene_v89 as v89

    state, wave1, wave2 = v89.validate_source(v89.partial_runtime.alg)
    assert state["accepted_rounds"][v89.SCENE] == 5
    assert wave1["status"] == "PASS"
    assert wave2["empty_candidate_wave"] is True
