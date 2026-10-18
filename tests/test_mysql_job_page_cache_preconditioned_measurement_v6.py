from scripts import mysql_job_page_cache_preconditioned_measurement_v6 as subject
from scripts import run_mysql_algorithm_a_job_page_cache_fixed_v6 as runner


def test_contract_keeps_measurement_and_bounds_startup_retry():
    contract = subject._validate_contract()
    assert contract["minimum_resident_fraction"] == 1.0
    assert contract["formal_measurement_replays_per_candidate"] == 1
    assert contract["prestart_read_only_retry_seconds"] == 600
    assert contract["prestart_retry_interval_seconds"] == 0.5
    assert contract["outer_loop_semantics_changed"] is False


def test_only_exact_prestart_blocker_is_retryable():
    assert runner.BLOCKED_PREFIX == "fresh R0 point non-interference gate blocked:"
    assert runner.RETRY_SECONDS == 600.0
    assert runner.RETRY_INTERVAL_SECONDS == 0.5
