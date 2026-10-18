from pathlib import Path


def test_v85_actual_runtime_chain_binds_collision_free_reset() -> None:
    from scripts import reset_mysql_xp_data_planes_mixed_four_worker_v4 as reset_v4
    from scripts import run_mysql_algorithm_c_async_scene_v85 as v85

    v84 = v85.predecessor
    v83 = v84.predecessor
    v82 = v83.predecessor
    v81 = v82.predecessor
    v80 = v81.predecessor
    v79 = v80.predecessor
    v78 = v79.predecessor
    v77 = v78.predecessor
    v76 = v77.predecessor

    v80.CONTRACT_ROOT = v85.CONTRACT_ROOT
    v80.PARENT_CONTRACT = v85.PARENT_CONTRACT
    v80.OPTIMIZATION_CONTRACT = v85.OPTIMIZATION_CONTRACT
    v80.CONTRACT = v85.CONTRACT
    v80.validate_contract = v85.validate_contract
    v76.mixed_reset = reset_v4
    v76.install_mixed_runtime()
    v80.coverage_runtime.OPTIMIZATION_CONTRACT = v85.OPTIMIZATION_CONTRACT

    alg = v80.coverage_runtime.alg
    optimization = alg.load(v85.OPTIMIZATION_CONTRACT)
    contract = alg.load(v85.CONTRACT)
    actual_reset = Path(alg.reset.__file__).resolve()

    assert actual_reset == Path(reset_v4.__file__).resolve()
    assert optimization["bindings"]["reset_source_path"] == str(
        actual_reset.relative_to(v85.ROOT)
    )
    assert optimization["bindings"]["reset_source_sha256"] == alg.sha(actual_reset)
    assert contract["run_scoped_quarantine_successor"]["path_components"] == [
        "run_id", "outer", "batch_kind", "reset_token", "worker_id"
    ]
    boundary = contract["algorithm_a_independent_check_boundary"]
    assert boundary["algorithm_a_role"] == "independent_posthoc_check_only"
    assert boundary["algorithm_a_feedback_to_b_or_c"] is False


def test_v4_quarantine_path_keeps_run_outer_batch_and_token() -> None:
    path = Path(
        "/history/run_v1/outer_018/current_interval_plan_coverage/"
        "outer_018_current_interval_plan_coverage_1"
    )
    target = (
        Path("/disk/runtime") / "quarantine"
        / path.parent.parent.parent.name
        / path.parent.parent.name
        / path.parent.name
        / path.name
        / "xp_cpu_w3"
    )
    assert str(target) == (
        "/disk/runtime/quarantine/run_v1/outer_018/"
        "current_interval_plan_coverage/"
        "outer_018_current_interval_plan_coverage_1/xp_cpu_w3"
    )
