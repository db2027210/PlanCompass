import copy
from pathlib import Path

import pytest

import mysql_algorithm_b_policy_v2 as policy
from scripts import (
    launch_mysql_four_scene_algorithm_b_independent_resume_outer_plan_v5 as launcher,
)
from scripts import run_mysql_four_scene_algorithm_b_independent_resume_outer_plan_v5 as resume


SOURCE = (
    Path(__file__).resolve().parents[1]
    / "history/mysql_four_scene_20260809"
    / "v5_formal_independent_model_gate_resume_v63_20260818_attempt001"
)


def test_outer_plan_resume_reuses_all_fifteen_verified_waves_and_gates() -> None:
    evidence = resume.validate_resume_source(SOURCE)
    assert resume.RESUME_CALIBRATION_WAVES == 15
    assert set(evidence["wave_rows"]) == set(range(1, 16))
    assert evidence["state"]["outer_attempt"] == 0
    assert evidence["failure_evidence_path"].name == "run_state.json"
    assert len(evidence["quality_gate_evidence"]) == 4


def test_policy_accepts_exact_amended_design_and_rejects_old_design() -> None:
    params_by_scene, ranges_by_scene, active_by_scene = resume.parameter_state()
    scene = "tpch_sf4_memory"
    ranges = resume.transformed_ranges(
        ranges_by_scene[scene], active_by_scene[scene]
    )
    amended = copy.deepcopy(
        resume.alg.load(SOURCE / "calibration_design.json")["scene_designs"][scene]
    )
    validated = policy._validated_design_override(
        amended, params_by_scene[scene], ranges, active_by_scene[scene]
    )
    assert validated == amended
    original = resume.mysql_fresh_model_calibration_points(
        params_by_scene[scene], ranges, active_by_scene[scene], replay_budget=15
    )
    assert original[10]["params"] != amended[10]["params"]
    duplicate = copy.deepcopy(amended)
    duplicate[14]["params"] = copy.deepcopy(duplicate[10]["params"])
    with pytest.raises(RuntimeError, match="not distinct"):
        policy._validated_design_override(
            duplicate, params_by_scene[scene], ranges, active_by_scene[scene]
        )


def test_quality_gate_consumes_all_amended_points(tmp_path: Path) -> None:
    params_by_scene, ranges_by_scene, active_by_scene = resume.parameter_state()
    scene = "tpch_sf4_memory"
    ranges = resume.transformed_ranges(
        ranges_by_scene[scene], active_by_scene[scene]
    )
    design = resume.alg.load(SOURCE / "calibration_design.json")["scene_designs"][scene]
    consumed = []

    class Baseline:
        def as_resource(self):
            return {"cpu": 1.0, "memory": 1.0, "io": 1.0}

    class Objective:
        baseline = Baseline()

        def resource_score_details(self, _measurement):
            return {"total": 1.0}

    class Model:
        objective = Objective()

        def build_experience(self, params, _resource, _raw=None):
            return {"params": dict(params)}

        def add(self, *_args):
            return None

        def select_quality_mode(self, *_args, **_kwargs):
            return {"passed": True, "reason": "test"}

    def validator(_sql_set, point):
        consumed.append(resume.alg.optimizer_params_key(point))
        return {
            "db_qps": 1.0,
            "resources": {"cpu": 1.0, "memory": 1.0, "io": 1.0},
        }

    result = policy.run_mysql_fresh_model_quality_gate(
        Model(), validator, {}, params_by_scene[scene], ranges,
        active_by_scene[scene], scenario_dir=tmp_path,
        design_override=design,
    )
    assert result["frozen_design_override_consumed"] is True
    assert result["usable_sample_count_including_baseline"] == 16
    assert len(consumed) == 15
    assert len(set(consumed)) == 15


def test_worker_plan_log_binding_is_output_scoped(tmp_path: Path) -> None:
    source = {"worker_id": "job-a", "scene": "job_memory"}
    plan_log = tmp_path / "outer_001/plan_capture/job_memory/client_processes.jsonl"
    bound = resume.worker_with_plan_log(source, plan_log)
    assert "formal_client_log" not in source
    assert bound["formal_client_log"] == str(plan_log)
    assert plan_log.parent.is_dir()


def test_outer_plan_launcher_binds_v6_release() -> None:
    root = Path(__file__).resolve().parents[1]
    assert launcher.CONTROLLER == (
        root / "scripts/run_mysql_four_scene_algorithm_b_independent_resume_outer_plan_v5.py"
    )
    assert launcher.CONTRACT.parent.name == (
        "frozen_mysql_algorithm_b_independent_resume_outer_plan_20260818_v6"
    )
