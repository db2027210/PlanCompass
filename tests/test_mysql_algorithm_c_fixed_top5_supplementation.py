from __future__ import annotations

from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v2 as controller
from scripts import run_mysql_four_scene_algorithm_c_v2 as alg


GOOD_PLAN = "a" * 64
BAD_PLAN = "b" * 64


def _row(value: float, score: float, plan: str = GOOD_PLAN) -> dict:
    return {
        "subset_id": f"p_{value}",
        "candidate_step_fraction": 0.08,
        "optimized_params": {"p": value},
        "candidate_score": score,
        "candidate_score_before_confidence": score,
        "resource_model": {"candidate_plan_sha256": plan},
        # Deliberately conflicting post-replay evidence: it must not affect score.
        "db_qps": 1_000_000.0 - value,
    }


def _hard_prune_history() -> list[dict]:
    return [
        {
            "full_plan_sha256": BAD_PLAN,
            "outer_round": outer,
            "parameter_point_key": f"point-{outer}",
            "accepted": False,
            "qps_nondecline_counterexample": False,
            "learnable_qps_decline": True,
        }
        for outer in (1, 2)
    ]


def test_supplements_then_returns_partial_topk_without_duplicate_plan_padding() -> None:
    base = [
        _row(1.0, 9.0), _row(2.0, 8.0), _row(3.0, 7.0),
        _row(3.0, 6.0), _row(4.0, 100.0, BAD_PLAN),
    ]
    supplied: list[tuple[float, str]] = []

    def factory(fraction: float, direction_mode: str) -> list[dict]:
        supplied.append((fraction, direction_mode))
        if fraction == 0.16:
            return [_row(5.0, 99.0, BAD_PLAN)]
        return [_row(10.0 + fraction, 5.0 - fraction)]

    combined, selected, audit, hard_pruned, _ = (
        controller.supplement_and_select_fixed_top_k(
            base_rankings=base,
            plan_history=_hard_prune_history(),
            replayed_param_keys=set(),
            permanently_invalid_param_keys=set(),
            supplementary_factory=factory,
        )
    )

    assert len(combined) >= 7
    assert len(selected) == 1
    assert supplied == [
        (0.16, "gradient"),
        (0.08, "inward_if_boundary_collapsed"),
        (0.16, "inward_if_boundary_collapsed"),
        (0.24, "inward_if_boundary_collapsed"),
    ]
    assert hard_pruned
    assert all(
        row["resource_model"]["candidate_plan_sha256"] != BAD_PLAN
        for row in selected
    )
    supplement = audit["candidate_pool_supplementation_v1"]
    assert supplement["top_k_limit"] == 5
    assert supplement["top_k_is_upper_bound"] is True
    assert supplement["partial_top_k"] is True
    assert supplement["duplicate_plan_padding_allowed"] is False
    assert supplement["hard_pruning_bypassed"] is False
    assert supplement["candidate_score_mutated"] is False
    assert supplement[
        "qps_used_for_generation_resource_prediction_gradient_or_score"
    ] is False
    assert [row["candidate_score"] for row in selected] == [9.0]


def test_candidate_step_fraction_stays_inside_frozen_range(monkeypatch) -> None:
    monkeypatch.setattr(alg, "load", lambda _path: {
        "parameter_space": {"active_params": ["p"]},
        "table": {"p": {"memory": -1.0, "cpu": 0.0, "io": 0.0}},
    })
    monkeypatch.setitem(alg.TARGET, "job_memory", "memory")
    monkeypatch.setattr(alg, "apply_costs", lambda _worker, params: dict(params))
    monkeypatch.setattr(alg, "capture_scene_plans", lambda *_args: {"plan": "x"})
    monkeypatch.setattr(alg, "relation_stats", lambda _worker: {})
    monkeypatch.setattr(alg, "executor_parameters", lambda _worker: {})
    monkeypatch.setattr(alg, "enhance_resource_prediction", lambda **kwargs: {
        "predicted_resources": kwargs["experience_prediction"],
        "confidence": 1.0,
        "candidate_plan_sha256": GOOD_PLAN,
    })

    rows = alg.candidate_rankings(
        "job_memory", {"p": 2.0}, {"p": {"lower": 1.0, "upper": 4.0}},
        {"cpu": 0.4, "memory": 0.7, "io": 0.3}, freeze={},
        worker={"worker_id": "test"}, step_fraction=0.40,
    )

    assert len(rows) == 1
    assert 1.0 <= rows[0]["optimized_params"]["p"] <= 4.0
    assert rows[0]["candidate_step_fraction"] == 0.40


def test_equivalent_subsets_are_explained_once(monkeypatch) -> None:
    monkeypatch.setattr(alg, "load", lambda _path: {
        "parameter_space": {"active_params": ["p", "q"]},
        "table": {
            "p": {"memory": -1.0, "cpu": 0.0, "io": 0.0},
            "q": {"memory": -1.0, "cpu": 0.0, "io": 0.0},
        },
    })
    monkeypatch.setattr(alg, "apply_costs", lambda _worker, params: dict(params))
    captures: list[int] = []

    def capture(*_args):
        captures.append(1)
        return {"plan": "x"}

    monkeypatch.setattr(alg, "capture_scene_plans", capture)
    monkeypatch.setattr(alg, "relation_stats", lambda _worker: {})
    monkeypatch.setattr(alg, "executor_parameters", lambda _worker: {})
    monkeypatch.setattr(alg, "enhance_resource_prediction", lambda **kwargs: {
        "predicted_resources": kwargs["experience_prediction"],
        "confidence": 1.0,
        "candidate_plan_sha256": GOOD_PLAN,
    })

    rows = alg.candidate_rankings(
        "job_memory", {"p": 2.0, "q": 4.0},
        {
            "p": {"lower": 1.0, "upper": 4.0},
            "q": {"lower": 1.0, "upper": 4.0},
        },
        {"cpu": 0.4, "memory": 0.7, "io": 0.3}, freeze={},
        worker={"worker_id": "test"}, step_fraction=0.40,
    )

    assert len(rows) == 2
    assert captures == [1, 1, 1]  # one baseline plus two unique points
    assert sorted(row["equivalent_subset_count"] for row in rows) == [1, 2]
    assert all(row["raw_nonempty_subset_count"] == 3 for row in rows)


def test_formal_top_k_cannot_be_changed() -> None:
    try:
        controller.supplement_and_select_fixed_top_k(
            base_rankings=[], plan_history=[], replayed_param_keys=set(),
            permanently_invalid_param_keys=set(),
            supplementary_factory=lambda _f, _mode: [],
            top_k=4,
        )
    except ValueError as exc:
        assert "fixed top-k" in str(exc)
    else:
        raise AssertionError("Top-4 must be rejected")


def test_boundary_collapsed_gradient_uses_inward_fallback() -> None:
    calls: list[tuple[float, str]] = []

    def factory(fraction: float, direction_mode: str) -> list[dict]:
        calls.append((fraction, direction_mode))
        if direction_mode == "gradient":
            return [_row(1.0, 8.0)]
        return [_row(float(value), 7.0 - value / 10.0) for value in range(2, 7)]

    _, selected, audit, _, _ = controller.supplement_and_select_fixed_top_k(
        base_rankings=[_row(1.0, 9.0)],
        plan_history=[], replayed_param_keys=set(),
        permanently_invalid_param_keys=set(), supplementary_factory=factory,
    )

    assert len(selected) == 1
    assert audit["candidate_pool_supplementation_v1"]["partial_top_k"] is True
    assert calls == [
        (0.16, "gradient"),
        (0.08, "inward_if_boundary_collapsed"),
    ]
    assert audit["candidate_pool_supplementation_v1"][
        "inward_fallback_only_for_boundary_collapsed_parameters"
    ] is True
