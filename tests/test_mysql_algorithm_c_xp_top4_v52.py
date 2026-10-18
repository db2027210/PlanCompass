from scripts import run_mysql_algorithm_c_async_scene_v52 as release


def test_v52_contract_and_scene_specific_top4_tables() -> None:
    contract = release.validate_contract()
    successor = contract["xp_top4_parameter_successor"]
    assert successor["active_parameters_by_scene"] == release.EXPECTED_ACTIVE
    assert successor["candidate_subset_count_by_scene"] == {
        "hybench_xp_cpu": 15,
        "hybench_xp_io": 15,
    }
    assert successor["real_top_k_limit"] == 5
    assert successor["algorithm_c_outer_flow_changed"] is False
