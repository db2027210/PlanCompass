from __future__ import annotations

from pathlib import Path


def test_v88_rebinds_post_fallback_to_current_four_worker_baseline() -> None:
    from scripts import build_mysql_algorithm_c_outer_checkpoint_v4 as checkpointmod
    from scripts import run_mysql_algorithm_c_async_scene_v88 as v88

    checkpoint = checkpointmod.verify_checkpoint(
        v88.ROOT / "history/mysql_four_scene_20260809/v992_to_c_cpu_outer17_docker_timeout_checkpoint_20260828/prepared_binding.json"
    )
    reconstructed = checkpoint["reconstructed_state"]
    result = v88.validate_post_fallback_partial(
        source_root=v88.predecessor.SOURCE_ROOT,
        checkpoint=checkpoint,
        reconstructed=reconstructed,
        start_rank=5,
    )
    assert result is not None
    baseline = v88.predecessor.partial_runtime.alg.load(Path(result["baseline"]["path"]))
    assert set(baseline["measurements"]) == {
        "xp_cpu_w1", "xp_cpu_w2", "xp_cpu_w3", "xp_cpu_w4"
    }
    assert all(row["db_qps"] == reconstructed["incumbents"][v88.SCENE]["db_qps"] for row in baseline["measurements"].values())
    assert result["checkpoint_incumbent_baseline_carry_forward"] is True
    assert result["accepted_state_credit_before_continuation"] == 0
