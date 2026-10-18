"""Regression for PG first-miss expansion commit semantics."""

from scripts import mysql_algorithm_c_interval_control_v2 as mysql_interval
from scripts import mysql_outer_rejection_cycle_v3 as mysql_rejection
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v55 as runtime


def test_effective_runtime_binds_pg_aligned_successors() -> None:
    assert runtime.interval_control is mysql_interval
    assert runtime.rejection_cycle is mysql_rejection


def test_first_miss_parent_is_restored_only_when_expansion_is_rejected() -> None:
    """An accepted expansion retains its accepted binary shrink, as PG does."""
    source = runtime._source
    expected = '''parent_ranges = state.setdefault(
                        "algorithm_c_first_miss_parent_ranges", {}
                    ).pop(scene, None)
                    if not accepted and parent_ranges is not None:
                        ranges_by_scene[scene] = parent_ranges'''
    assert expected in source
    assert '''if parent_ranges is not None:
                        ranges_by_scene[scene] = parent_ranges''' not in source
