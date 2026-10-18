#!/usr/bin/env python3
"""Contract tests for same-plan numeric refinement after plan exhaustion.

The ordinary Algorithm C Top-5 remains plan-cluster based: one representative
per complete physical-plan fingerprint.  A same-plan/different-parameter point
is eligible only after exhaustive search proves that the current formal
interval has no unseen plan tree left.  That exceptional point is a numeric
refinement, not a baseline/control/uncertain probe and not a relaxation of the
fallback plan-tree expansion contract.
"""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from experience_table_subset_optimizer import IncompleteSubsetOptimizationError  # noqa: E402
from gradient_descent_optimizer import ParameterRange  # noqa: E402
from run_pg_algorithm_c_scene import (  # noqa: E402
    ACTIVE_PARAMS,
    DEFAULT_RANGES,
    LaneController,
    assess_valid_replay_identity,
    distinct_validated_behavior_rankings,
    empty_valid_replay_identity_memory,
    exact_param_signature,
    record_valid_replay_identity,
    validate_zero_gradient_delta,
)


INITIAL_PARAMS = {
    "seq_page_cost": 1.0,
    "random_page_cost": 4.0,
    "cpu_tuple_cost": 0.01,
    "cpu_index_tuple_cost": 0.005,
    "cpu_operator_cost": 0.0025,
    "parallel_setup_cost": 1000.0,
    "parallel_tuple_cost": 0.1,
}


def candidate(
    seq_page_cost: float,
    plan_id: str,
    score: float,
    *,
    candidate_origin: str = "gradient_probe",
    zero_gradient: bool = False,
) -> dict:
    """Return a complete formal-candidate audit row for contract testing."""

    params = copy.deepcopy(INITIAL_PARAMS)
    params["seq_page_cost"] = seq_page_cost
    absolute_delta = seq_page_cost - INITIAL_PARAMS["seq_page_cost"]
    gradients = {name: 0.0 for name in ACTIVE_PARAMS}
    if not zero_gradient:
        gradients["seq_page_cost"] = 1.0
    delta_param = {
        name: {
            "before": INITIAL_PARAMS[name],
            "after_clip": params[name],
            "absolute": params[name] - INITIAL_PARAMS[name],
        }
        for name in ACTIVE_PARAMS
    }
    return {
        "subset_id": f"seq-{seq_page_cost:.6f}-{plan_id}",
        "candidate_origin": candidate_origin,
        "probe_kind": (
            "uncertain_probe"
            if candidate_origin == "uncertain_probe"
            else "gradient_relative_step_probe"
        ),
        "used_for_gradient": candidate_origin == "gradient_probe",
        "boundary_plateau": False,
        "active_subset": ["seq_page_cost"],
        "initial_params": copy.deepcopy(INITIAL_PARAMS),
        "optimized_params": params,
        "feature_ratio": {"cpu": 0.99, "memory": 1.0, "io": 1.0},
        "predicted_resource": {"cpu": 0.99, "memory": 1.0, "io": 1.0},
        "utilization": {"cpu": 0.99, "memory": 1.0, "io": 1.0},
        "dScore/dU": {"cpu": -1.0, "memory": 0.0, "io": 0.0},
        "T": {name: 1.0 for name in ACTIVE_PARAMS},
        "confidence": {name: 1.0 for name in ACTIVE_PARAMS},
        "g_j": gradients,
        "eta": abs(absolute_delta),
        "delta_param": delta_param,
        "candidate_score": score,
        "three_level_fingerprint": {
            "id": f"three-level:{seq_page_cost:.17g}:{plan_id}",
            "valid": True,
            "plan_level": plan_id,
        },
    }


def controller_with_identity_memory() -> LaneController:
    controller = LaneController.__new__(LaneController)
    controller.target = "cpu"
    controller.state = {
        "incumbent_params": copy.deepcopy(INITIAL_PARAMS),
        "incumbent_validation": {},
        "valid_replay_identity_memory": empty_valid_replay_identity_memory(
            "workload-sha",
            "cpu",
        ),
    }
    controller.ranges = {
        name: ParameterRange(low, high)
        for name, (low, high) in DEFAULT_RANGES.items()
    }
    controller.experience_artifact = {
        "resource_prediction_calibration": {"resources": {}}
    }
    controller.log = mock.Mock()
    return controller


class SamePlanNumericRefinementContractTest(unittest.TestCase):
    def test_base_top5_still_keeps_one_representative_per_complete_plan(self) -> None:
        rows = [
            candidate(1.10, "plan-a", 9.0),
            candidate(1.20, "plan-a", 8.0),
            candidate(1.30, "plan-b", 7.0),
            candidate(1.40, "plan-c", 6.0),
            candidate(1.50, "plan-c", 5.0),
        ]

        selected, duplicate_subset_ids = distinct_validated_behavior_rankings(rows)

        self.assertEqual(
            ["plan-a", "plan-b", "plan-c"],
            [row["three_level_fingerprint"]["plan_level"] for row in selected],
        )
        self.assertEqual(
            [rows[1]["subset_id"], rows[4]["subset_id"]],
            duplicate_subset_ids,
        )

    def test_exhausted_plan_space_uses_unseen_exact_identities_for_numeric_refinement(
        self,
    ) -> None:
        controller = controller_with_identity_memory()
        replayed = candidate(1.10, "plan-a", 100.0)
        record_valid_replay_identity(
            controller.state["valid_replay_identity_memory"],
            candidate=replayed,
            round_index=1,
            label="already-valid",
        )

        # There is only one complete plan tree in the exhausted interval.  Its
        # already-valid exact identity must not hide five distinct parameter
        # vectors that still provide numeric refinement of that same plan.
        unseen = [
            candidate(value, "plan-a", 90.0 - index)
            for index, value in enumerate((1.20, 1.30, 1.40, 1.50, 1.60, 1.70))
        ]
        uncertain = candidate(
            1.80,
            "plan-a",
            200.0,
            candidate_origin="uncertain_probe",
        )
        baseline = candidate(1.0, "plan-a", 300.0)
        error = IncompleteSubsetOptimizationError(
            "current formal interval has one exhausted plan cluster",
            [],
            "/tmp/same-plan-numeric-refinement",
        )
        error.eligible_candidate_rankings = [
            baseline,
            uncertain,
            replayed,
            *unseen,
        ]

        selected = controller.merge_exhausted_partial_topk([error], round_index=2)

        self.assertEqual(5, len(selected), "numeric refinement remains Top-5 bounded")
        self.assertEqual(
            [1.20, 1.30, 1.40, 1.50, 1.60],
            [row["optimized_params"]["seq_page_cost"] for row in selected],
        )
        self.assertTrue(all(row.get("numeric_refinement") is True for row in selected))
        self.assertTrue(
            all(row.get("candidate_origin") == "gradient_probe" for row in selected),
            "numeric refinement must retain trusted gradient lineage",
        )
        self.assertTrue(
            all(
                assess_valid_replay_identity(
                    controller.state["valid_replay_identity_memory"],
                    row,
                )["seen_valid_replay"]
                is False
                for row in selected
            ),
            "an exact parameter+plan identity already replayed must stay excluded",
        )
        self.assertEqual(
            len(selected),
            len(
                {
                    (
                        exact_param_signature(row["optimized_params"]),
                        row["three_level_fingerprint"]["plan_level"],
                    )
                    for row in selected
                }
            ),
            "numeric refinement cannot duplicate an exact parameter+plan pair",
        )

    def test_top5_validator_accepts_only_explicit_same_plan_numeric_refinement(
        self,
    ) -> None:
        controller = controller_with_identity_memory()
        left = candidate(1.20, "plan-a", 2.0)
        right = candidate(1.30, "plan-a", 1.0)
        left["numeric_refinement"] = True
        right["numeric_refinement"] = True

        # Duplicate complete plans are legal only for the explicit numeric
        # refinement exception and only when their parameter vectors differ.
        controller.validate_top5_audit_records([left, right])

        exact_duplicate = copy.deepcopy(left)
        exact_duplicate["subset_id"] = "duplicated-exact-identity"
        with self.assertRaisesRegex(RuntimeError, "duplicate.*parameter.*plan|identity"):
            controller.validate_top5_audit_records([left, exact_duplicate])

    def test_fallback_expansion_remains_plan_tree_change_only(self) -> None:
        controller = controller_with_identity_memory()
        controller.state["incumbent_validation"] = {
            "candidate_ranking": {
                "three_level_fingerprint": {"plan_level": "incumbent-plan"}
            }
        }
        base_rows = [
            {"candidate_ranking": candidate(1.10, "replayed-plan", 10.0)}
        ]
        rankings = [
            candidate(1.20, "replayed-plan", 9.0),
            candidate(1.30, "incumbent-plan", 8.0),
            candidate(1.40, "new-plan-a", 7.0),
            candidate(1.50, "new-plan-a", 6.0),
            candidate(1.60, "new-plan-b", 5.0),
        ]

        selected = controller.fallback_expansion_candidates(rankings, base_rows)

        self.assertEqual(
            ["new-plan-a", "new-plan-b"],
            [row["three_level_fingerprint"]["plan_level"] for row in selected],
        )
        self.assertTrue(all(row.get("numeric_refinement") is not True for row in selected))

    def test_all_zero_gradient_cannot_manufacture_numeric_refinement(self) -> None:
        invalid = candidate(1.20, "plan-a", 1.0, zero_gradient=True)
        invalid["numeric_refinement"] = True

        with self.assertRaisesRegex(RuntimeError, "all-zero g_j"):
            validate_zero_gradient_delta(invalid, 1)


if __name__ == "__main__":
    unittest.main()
