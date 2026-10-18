from pathlib import Path


def test_v84_actual_runtime_chain_binds_reset_v3_and_forbids_a_prior() -> None:
    from scripts import reset_mysql_xp_data_planes_mixed_four_worker_v3 as reset_v3
    from scripts import run_mysql_algorithm_c_async_scene_v84 as v84

    v83 = v84.predecessor
    v82 = v83.predecessor
    v81 = v82.predecessor
    v80 = v81.predecessor
    v79 = v80.predecessor
    v78 = v79.predecessor
    v77 = v78.predecessor
    v76 = v77.predecessor

    v80.CONTRACT_ROOT = v84.CONTRACT_ROOT
    v80.PARENT_CONTRACT = v84.PARENT_CONTRACT
    v80.OPTIMIZATION_CONTRACT = v84.OPTIMIZATION_CONTRACT
    v80.CONTRACT = v84.CONTRACT
    v80.validate_contract = v84.validate_contract
    v76.mixed_reset = reset_v3
    v76.install_mixed_runtime()
    v80.coverage_runtime.OPTIMIZATION_CONTRACT = v84.OPTIMIZATION_CONTRACT

    alg = v80.coverage_runtime.alg
    optimization = alg.load(v84.OPTIMIZATION_CONTRACT)
    contract = alg.load(v84.CONTRACT)
    bindings = optimization["bindings"]
    actual_reset = Path(alg.reset.__file__).resolve()

    assert actual_reset == Path(reset_v3.__file__).resolve()
    assert bindings["reset_source_path"] == str(actual_reset.relative_to(v84.ROOT))
    assert bindings["reset_source_sha256"] == alg.sha(actual_reset)
    assert bindings["algorithm_source_sha256"] == alg.sha(Path(alg.__file__).resolve())
    assert optimization["optimized_reset"]["reset_concurrency"] == 4
    boundary = contract["algorithm_a_independent_check_boundary"]
    assert boundary["algorithm_a_role"] == "independent_posthoc_check_only"
    assert boundary["algorithm_a_feedback_to_b_or_c"] is False
    assert (
        boundary["algorithm_a_candidate_qps_range_or_ranking_prior_used_by_b_or_c"]
        is False
    )
