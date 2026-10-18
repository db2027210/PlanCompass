from __future__ import annotations


def test_v90_contract_and_exact_source_validate() -> None:
    from scripts import run_mysql_algorithm_c_async_scene_v90 as v90

    contract = v90.validate_contract()["successor"]
    hook = contract["exact_boundary_fallback_reuse_hook"]
    assert hook["scene"] == "hybench_xp_io"
    assert hook["physical_fallback_remeasurement"] is False
    assert hook["accepted_or_rejected_credit"] == 0
    state, wave1, wave2 = v90.predecessor.validate_source(v90.predecessor.partial_runtime.alg)
    assert state["accepted_rounds"]["hybench_xp_io"] == 5
    assert wave1["status"] == "PASS"
    assert wave2["empty_candidate_wave"] is True
