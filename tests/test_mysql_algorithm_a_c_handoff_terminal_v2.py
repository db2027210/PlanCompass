from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import launch_mysql_algorithm_a_async_v1 as launcher


SCENE = "tpch_sf4_memory"


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _state() -> dict:
    return {
        "algorithm": "C",
        "async_scene": SCENE,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "status": "NON_CONVERGED",
        "stage": "FINISHED",
        "completion_reason": "candidate_space_or_max_rounds_exhausted",
        "outer_attempt": 14,
        "accepted_rounds": {SCENE: 10},
        "converged": {SCENE: True},
        "formal_evidence_eligible": True,
        "post_search_stability_confirmation": False,
        "other_scene_completion_required": False,
    }


def _decision() -> dict:
    return {
        "scene": SCENE,
        "outer_attempt": 14,
        "accepted": True,
        "accepted_rounds": 10,
        "formal_evidence_eligible": True,
        "interval_convergence": {
            "contract": "pg_accepted_count_terminal_v1",
            "converged": True,
            "post_search_stability_confirmation": False,
        },
    }


def test_legacy_aggregate_label_requires_terminal_decision(tmp_path: Path) -> None:
    state_path = tmp_path / "run_state.json"
    _write(state_path, _state())
    decision_path = tmp_path / "outer_014" / f"{SCENE}.decision.json"
    _write(decision_path, _decision())

    evidence = launcher.validate_c_completion({SCENE: state_path})[SCENE]

    assert evidence["terminal_mode"] == "legacy_aggregate_label_normalized"
    assert evidence["terminal_decision_path"] == str(decision_path)


def test_legacy_aggregate_label_rejects_unconverged_decision(tmp_path: Path) -> None:
    state_path = tmp_path / "run_state.json"
    _write(state_path, _state())
    decision = _decision()
    decision["interval_convergence"]["converged"] = False
    _write(tmp_path / "outer_014" / f"{SCENE}.decision.json", decision)

    with pytest.raises(RuntimeError, match="terminal decision differs"):
        launcher.validate_c_completion({SCENE: state_path})


def test_canonical_complete_does_not_require_compatibility_artifact(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "run_state.json"
    state = _state()
    state["status"] = "COMPLETE"
    state["completion_reason"] = "accepted_count_reached_10"
    _write(state_path, state)

    evidence = launcher.validate_c_completion({SCENE: state_path})[SCENE]

    assert evidence["terminal_mode"] == "canonical_complete"
