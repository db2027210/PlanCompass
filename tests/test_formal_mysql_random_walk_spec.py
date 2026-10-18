from formal_mysql_random_walk_spec import (
    EXPECTED_TARGETS,
    load_session_schedule,
    validate_against_frozen_matrices,
    validate_authoritative_files,
)


def test_authoritative_files_are_unchanged():
    assert len(validate_authoritative_files()) == 3


def test_schedule_contains_exactly_the_session_5_targets():
    specs = load_session_schedule()
    assert len(specs) == 14
    assert {(spec.run_id, spec.repeat_id) for spec in specs} == EXPECTED_TARGETS
    assert [spec.ordinal for spec in specs] == sorted(spec.ordinal for spec in specs)


def test_schedule_matches_frozen_experiment_and_benchmark_matrices():
    validate_against_frozen_matrices(load_session_schedule())
