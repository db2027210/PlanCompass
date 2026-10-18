from formal_mysql_recost_spec import (
    EXPECTED_ORDINALS,
    EXPECTED_TARGETS,
    load_recost_schedule,
    validate_against_frozen_matrices,
    validate_authoritative_files,
)


def test_authoritative_inputs_are_unchanged():
    assert len(validate_authoritative_files()) == 3


def test_exact_session_6_targets_and_global_ordinals():
    specs = load_recost_schedule()
    assert len(specs) == 14
    assert {(spec.run_id, spec.repeat_id) for spec in specs} == EXPECTED_TARGETS
    assert tuple(spec.ordinal for spec in specs) == EXPECTED_ORDINALS


def test_schedule_matches_frozen_experiment_and_benchmark_matrices():
    validate_against_frozen_matrices(load_recost_schedule())
