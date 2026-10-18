from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import reconcile_mysql_algorithm_c_async_terminal_v1 as subject


def write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def fixture(tmp_path: Path) -> Path:
    run = tmp_path / "run"
    write(run / "run_state.json", {
        "active_scenes": ["hybench_xp_cpu"],
        "async_scene": "hybench_xp_cpu",
        "status": "NON_CONVERGED",
        "stage": "FINISHED",
        "error": None,
        "outer_attempt": 20,
        "converged": {
            "hybench_xp_cpu": True,
            "hybench_xp_io": False,
            "job_memory": False,
            "tpch_sf4_memory": False,
        },
        "accepted_rounds": {"hybench_xp_cpu": 8},
        "rejected_outer_attempts": {"hybench_xp_cpu": 12},
        "termination_audit": None,
        "completion_reason": "candidate_space_or_max_rounds_exhausted",
    })
    write(run / "outer_020/hybench_xp_cpu.decision.json", {
        "scene": "hybench_xp_cpu",
        "outer_attempt": 20,
        "accepted_rounds": 8,
        "rejected_outer_attempts": 12,
        "interval_convergence": {
            "contract": "mysql_bc_qps_strict_two_reject_incumbent_shrink_v2",
            "converged": True,
            "accepted_rounds_are_not_inferred_from_forced_shrinks": True,
            "normalized_width": 0.0078125,
            "terminal_normalized_width": 0.01,
        },
    })
    return run


def test_reconciles_only_active_scene_without_replay(tmp_path: Path) -> None:
    result = subject.reconcile(fixture(tmp_path))
    assert result["status"] == "PASS"
    assert result["effective_status"] == "COMPLETE"
    assert result["physical_rerun_required"] is False
    assert result["new_physical_measurement_invoked"] is False
    assert len(result["inactive_scene_convergence_keys_ignored"]) == 3


def test_rejects_width_above_frozen_threshold(tmp_path: Path) -> None:
    run = fixture(tmp_path)
    decision = run / "outer_020/hybench_xp_cpu.decision.json"
    value = json.loads(decision.read_text(encoding="utf-8"))
    value["interval_convergence"]["normalized_width"] = 0.02
    write(decision, value)
    with pytest.raises(RuntimeError, match="terminal convergence evidence"):
        subject.reconcile(run)


def test_rejects_nonconverged_active_scene(tmp_path: Path) -> None:
    run = fixture(tmp_path)
    state = run / "run_state.json"
    value = json.loads(state.read_text(encoding="utf-8"))
    value["converged"]["hybench_xp_cpu"] = False
    write(state, value)
    with pytest.raises(RuntimeError, match="known converged"):
        subject.reconcile(run)
