from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import run_mysql_algorithm_a_partial_round_codefix_resume_v1 as resume


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_measurement_rows_accepts_list_and_preserves_mixed_wave() -> None:
    rows = resume.measurement_rows({
        "measurements": [
            {"worker_id": "w1", "rank": 51, "hard_gate_passed": False},
            {"worker_id": "w2", "rank": 52, "hard_gate_passed": True},
        ],
    }, {"w1", "w2"})
    assert rows["w1"]["hard_gate_passed"] is False
    assert rows["w2"]["hard_gate_passed"] is True


def test_validate_cached_round_requires_contiguous_physical_prefix(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "algorithm_a_state.json"
    candidate_path = tmp_path / "round_001" / "candidates.json"
    _write(candidate_path, {
        "round_index": 1,
        "candidate_count": 4,
        "candidates": [{}, {}, {}, {}],
    })
    for wave_index, ranks in ((1, [1, 2]), (2, [3, 4])):
        _write(
            tmp_path / "round_001" / f"wave_{wave_index:03d}"
            / "attempt_01" / "batch_result.json",
            {"measurements": [
                {"worker_id": "w1", "rank": ranks[0]},
                {"worker_id": "w2", "rank": ranks[1]},
            ]},
        )
    predecessor = {
        "round_checkpoint": {
            "candidate_count": 4,
            "attempted_ranks": [1, 2, 3, 4],
            "waves": [
                {"wave_index": 1, "attempt": 1, "ranks": [1, 2]},
                {"wave_index": 2, "attempt": 1, "ranks": [3, 4]},
            ],
        },
    }
    _write(state_path, predecessor)

    candidates, cached, attempted = resume.validate_cached_round(
        predecessor, state_path, 1,
    )
    assert candidates == candidate_path
    assert sorted(cached) == [1, 2]
    assert attempted == [1, 2, 3, 4]

    predecessor["round_checkpoint"]["attempted_ranks"] = [1, 2, 4]
    with pytest.raises(RuntimeError, match="contiguous prefix"):
        resume.validate_cached_round(predecessor, state_path, 1)


def test_candidate_equivalence_is_exact_for_executable_ranked_vectors(
    tmp_path: Path,
) -> None:
    left = tmp_path / "left.json"
    right = tmp_path / "right.json"
    document = {
        "algorithm": "A",
        "method": "m",
        "scene": "hybench_xp_io",
        "round_index": 1,
        "accepted_index": 0,
        "active_parameters": ["p"],
        "candidate_count": 2,
        "expected_candidate_count": 2,
        "canonical_rank_order_preserved": True,
        "candidates": [
            {
                "rank": 1, "optimized_params": {"p": 1.0},
                "resource_model": {"candidate_plan_sha256": "old-1"},
            },
            {
                "rank": 2, "optimized_params": {"p": 2.0},
                "resource_model": {"candidate_plan_sha256": "old-2"},
            },
        ],
    }
    _write(left, document)
    _write(right, document)
    assert resume.candidate_documents_equivalent(left, right)

    audit_only_changed = json.loads(json.dumps(document))
    audit_only_changed["candidates"][0]["resource_model"][
        "candidate_plan_sha256"
    ] = "new-1"
    _write(right, audit_only_changed)
    assert resume.candidate_documents_equivalent(left, right)

    changed = json.loads(json.dumps(document))
    changed["candidates"][0]["optimized_params"]["p"] = 1.5
    _write(right, changed)
    assert not resume.candidate_documents_equivalent(left, right)


def test_resumed_state_records_partial_round_reuse(tmp_path: Path) -> None:
    predecessor_path = tmp_path / "state.json"
    _write(predecessor_path, {"snapshot": True})
    state = resume.build_resumed_state(
        fresh={"algorithm": "A"},
        predecessor={
            "run_id": "old_xp_io", "accepted": 0,
            "rejected_rounds": 0, "consecutive_rejected_rounds": 0,
            "incumbent_reference_refresh_count": 0,
        },
        preserved={"incumbent_validation": {}, "baseline_by_worker": {}},
        completed_round=0, interrupted_round=1,
        predecessor_path=predecessor_path,
        cached_waves={1: {}}, attempted_ranks=[1, 2],
    )
    assert state["partial_interrupted_round_credited"] is True
    assert state["cached_physical_wave_evidence_reused"] is True
    assert state["cached_predecessor_attempted_ranks"] == [1, 2]


def test_partial_round_resume_contract_is_frozen() -> None:
    contract = resume.validate_partial_resume_contract()
    flow = contract["partial_round_recovery_flow"]
    assert flow["cached_attempted_prefix_physically_replayed"] is False
    assert flow["cached_attempted_prefix_reclassified_by_v4"] is True
    assert flow["new_physical_work_starts_after_cached_prefix"] is True
