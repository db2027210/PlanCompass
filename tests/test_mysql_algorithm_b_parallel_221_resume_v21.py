from scripts import (
    run_mysql_four_scene_algorithm_b_independent_parallel_221_resume_v21
    as subject,
)


def test_v21_contract_binds_subset_shrink_diversity_and_symmetric_risk():
    contract = subject.validate_contract()
    assert contract["range_shrink"]["untested_active_parameter_ranges_shrunk"] is False
    assert contract["candidate_diversity"]["qps_used"] is False
    assert contract["tpch_plan_ranking"]["symmetric_risk"] is True
    assert contract["candidate_batches"] == [[1, 2], [3, 4], [5]]


def test_historical_ranges_are_reconstructed_from_only_tested_subsets():
    corrected, events = subject.reconstruct_subset_only_ranges()
    assert [(row["outer_attempt"], row["scene"], row["selected_rank"]) for row in events] == [
        (4, "hybench_xp_cpu", 3),
        (4, "job_memory", 4),
        (6, "hybench_xp_io", 3),
    ]
    assert corrected["job_memory"]["memory_temptable_row_cost"] == {
        "lower": 0.01,
        "upper": 0.1,
    }
    assert corrected["job_memory"]["memory_block_read_cost"] == {
        "lower": 0.025,
        "upper": 2.5,
    }
    assert corrected["hybench_xp_cpu"]["io_block_read_cost"] == {
        "lower": 0.1,
        "upper": 10.0,
    }
    assert corrected["hybench_xp_cpu"]["key_compare_cost"] == {
        "lower": 0.005,
        "upper": 0.05,
    }
    assert corrected["hybench_xp_io"]["memory_block_read_cost"] == {
        "lower": 0.025,
        "upper": 0.25,
    }
    assert corrected["hybench_xp_io"]["io_block_read_cost"] == {
        "lower": 0.1,
        "upper": 10.0,
    }
