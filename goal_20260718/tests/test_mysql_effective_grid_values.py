from mysql_double_loop_grid_runner import mysql_effective_grid_values


def test_boolean_system_variable_uses_only_effective_values() -> None:
    assert mysql_effective_grid_values(
        "innodb_adaptive_hash_index", 0.0, 1.0, 11
    ) == (0.0, 1.0)


def test_small_integer_system_variable_deduplicates_fractional_aliases() -> None:
    assert mysql_effective_grid_values(
        "innodb_flush_log_at_trx_commit", 1.0, 2.0, 11
    ) == (1.0, 2.0)


def test_continuous_cost_parameter_keeps_regular_grid() -> None:
    assert mysql_effective_grid_values("io_block_read_cost", 1.0, 10.0, 11) is None


def test_wide_integer_range_preserves_requested_resolution_without_aliases() -> None:
    values = mysql_effective_grid_values("innodb_spin_wait_delay", 1.0, 10.0, 5)
    assert values == (1.0, 3.0, 6.0, 8.0, 10.0)
