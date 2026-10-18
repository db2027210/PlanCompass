from pathlib import Path
import hashlib
import json

from scripts import (
    run_mysql_four_scene_algorithm_b_independent_parallel_221_resume_v18
    as subject,
)


ROOT = Path(__file__).resolve().parents[1]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_v18_source_is_exact_completed_outer5_boundary():
    state = subject.validate_source()
    assert state["status"] == "RUNNING"
    assert state["outer_attempt"] == 5
    assert state["stage"] == "ALGORITHM_B_OUTER_006_RANK_1_RESETTING_XP"
    assert not (
        subject.SOURCE / "outer_006/top5_batch_1/batch_result.json"
    ).exists()


def test_v18_reuses_only_completed_outer1_through_outer5_batches():
    assert len(subject.REUSED_BATCHES) == 25
    assert sorted(subject.REUSED_BATCHES) == [
        (outer, rank) for outer in range(1, 6) for rank in range(1, 6)
    ]
    assert all(outer < 6 for outer, _rank in subject.REUSED_BATCHES)


def test_v18_recovery_and_q1_q22_plan_capture_are_bound():
    subject.validate_recovery_and_plan_capture()
    validation = json.loads(subject.PLAN_VALIDATION.read_text(encoding="utf-8"))
    assert validation["query_ids"] == list(range(1, 23))
    assert validation["read_only_explain_only"] is True
    assert validation["workload_executed"] is False


def test_v18_contract_source_hashes_and_semantics_match():
    contract = subject.validate_contract()
    assert contract["candidate_batches"] == [[1, 2], [3, 4], [5]]
    assert contract["convergence"] == {
        "accepted_shrinks_required_per_scene": 10,
        "fixed_real_top_k_per_scene": 5,
        "maximum_interval_ratio": 1.01,
        "required_consecutive_stable_accepted_rounds_per_scene": 2,
        "same_progress_across_scenes_required": False,
    }
    assert contract["acceptance_selection"]["effective_from_outer"] == 6
    assert contract["score_contract"][
        "qps_used_for_resource_prediction_gradient_or_score"
    ] is False
    for binding in contract["source_bindings"].values():
        path = ROOT / binding["path"]
        assert path.is_file() and not path.is_symlink()
        assert _sha(path) == binding["sha256"]

