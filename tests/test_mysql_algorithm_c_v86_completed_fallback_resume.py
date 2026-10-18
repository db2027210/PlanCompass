from __future__ import annotations


def test_v86_source_is_exact_completed_fallback_without_state_credit() -> None:
    from scripts import run_mysql_algorithm_c_async_scene_v86 as v86

    v84 = v86.predecessor.predecessor
    v83 = v84.predecessor
    v82 = v83.predecessor
    v81 = v82.predecessor
    v80 = v81.predecessor
    alg = v80.coverage_runtime.alg
    state, wave1, wave2 = v86.validate_source(alg)
    assert state["accepted_rounds"][v86.SCENE] == 6
    assert state["consecutive_failed_rounds"][v86.SCENE] == 1
    assert wave1["status"] == "PASS"
    assert sum(int(row.get("rank") or 0) > 0 for row in wave1["measurements"].values()) == 1
    assert wave2["empty_candidate_wave"] is True
    contract = alg.load(v86.CONTRACT)
    reuse = contract["completed_fallback_measurement_continuation"]
    assert reuse["completed_top5_physically_replayed"] is False
    assert reuse["completed_fallback_physically_replayed"] is False
    assert reuse["accepted_or_rejected_credit_for_recovery"] == 0
    assert reuse["algorithm_a_input_used"] is False
