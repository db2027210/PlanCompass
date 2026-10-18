from __future__ import annotations

from scripts import mysql_algorithm_c_interval_control_v2 as interval_control
from scripts import mysql_algorithm_c_pg_recovery_v1 as pg_recovery
from scripts import mysql_outer_rejection_cycle_v3 as rejection_cycle
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v57 as runtime


def test_expanded_runtime_binds_every_overlay_dependency():
    assert runtime.interval_control is interval_control
    assert runtime.pg_recovery is pg_recovery
    assert runtime.rejection_cycle is rejection_cycle


def test_first_decision_path_can_resolve_interval_checks():
    before = {
        "a": {"lower": 1.0, "upper": 100.0},
        "b": {"lower": 1.0, "upper": 100.0},
    }
    after, progress = runtime.rejection_cycle.advance_rejection_cycle(
        accepted=False,
        consecutive_rejections=1,
        ranges=before,
        incumbent_params={"a": 10.0, "b": 10.0},
        active_params=["a", "b"],
    )
    assert progress["forced_incumbent_shrink"] is True
    audit = runtime.interval_control.require_binary_progress(
        before, after, ["a", "b"],
    )
    assert audit["strictly_shrunk"] == ["a", "b"]
