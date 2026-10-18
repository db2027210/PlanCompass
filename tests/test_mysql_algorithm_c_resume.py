from __future__ import annotations

from pathlib import Path

from scripts import run_mysql_four_scene_algorithm_c_resume_v1 as resume


ROOT = Path(__file__).resolve().parents[1]


def _workers() -> dict[str, dict[str, str]]:
    return {
        "job_w1": {"scene": "job_memory"},
        "job_w2": {"scene": "job_memory"},
        "tpch_w1": {"scene": "tpch_sf4_memory"},
        "tpch_w2": {"scene": "tpch_sf4_memory"},
        "xp_cpu_w1": {"scene": "hybench_xp_cpu"},
        "xp_cpu_w2": {"scene": "hybench_xp_cpu"},
        "xp_io_w1": {"scene": "hybench_xp_io"},
        "xp_io_w2": {"scene": "hybench_xp_io"},
    }


def test_checkpoint_reconstructs_completed_outer1() -> None:
    path = (
        ROOT / "history/mysql_four_scene_20260809"
        / "algorithm_c_outer1_checkpoint_v1_20260811T184000p0800"
        / "prepared_binding.json"
    )
    _checkpoint, restored = resume.load_checkpoint(path)
    assert restored["accepted_rounds"] == {
        "job_memory": 1,
        "tpch_sf4_memory": 0,
        "hybench_xp_cpu": 0,
        "hybench_xp_io": 1,
    }
    assert restored["rejected_outer_attempts"] == {
        "job_memory": 0,
        "tpch_sf4_memory": 1,
        "hybench_xp_cpu": 1,
        "hybench_xp_io": 0,
    }
    assert len(restored["replayed_param_keys_by_scene"]["tpch_sf4_memory"]) == 5
    assert len(restored["replayed_param_keys_by_scene"]["hybench_xp_cpu"]) == 5


def test_converged_scene_is_assigned_as_full_workload_companion() -> None:
    rankings = {
        scene: [
            {"optimized_params": {"p": float(rank)}} for rank in range(1, 6)
        ]
        for scene in resume.SCENES
        if scene != "tpch_sf4_memory"
    }
    params = {scene: {"p": 99.0} for scene in resume.SCENES}
    assignments, companions = resume.build_batch_assignments(
        rankings=rankings, params_by_scene=params, workers=_workers(), ranks=(1, 2)
    )
    assert set(assignments) == set(_workers())
    assert companions == {"tpch_w1", "tpch_w2"}
    assert assignments["tpch_w1"] == {"rank": 0, "params": {"p": 99.0}}
    assert assignments["tpch_w2"] == {"rank": 0, "params": {"p": 99.0}}
    assert assignments["job_w1"] == {"rank": 1, "params": {"p": 1.0}}
    assert assignments["job_w2"] == {"rank": 2, "params": {"p": 2.0}}


def test_rank5_batch_runs_one_complete_copy_of_each_scene() -> None:
    rankings = {
        scene: [
            {"optimized_params": {"p": float(rank)}} for rank in range(1, 6)
        ]
        for scene in resume.SCENES
    }
    assignments, companions = resume.build_batch_assignments(
        rankings=rankings,
        params_by_scene={scene: {"p": 99.0} for scene in resume.SCENES},
        workers=_workers(),
        ranks=(5,),
    )
    assert companions == set()
    assert set(assignments) == {"job_w1", "tpch_w1", "xp_cpu_w1", "xp_io_w1"}
    assert all(row["rank"] == 5 for row in assignments.values())
