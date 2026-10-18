#!/usr/bin/env python3
"""Regression tests for the fail-closed real-replay direction ranker."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from real_replay_direction_ranker import (  # noqa: E402
    TASK_JOINT,
    TASK_QPS_UP,
    TASK_TARGET_DOWN,
    DirectionRankerConfig,
    ReplayRankingCandidate,
    ReplayRankingSample,
    direction_ranker_from_dict,
    train_real_replay_direction_ranker,
    training_result_to_json,
)


def balanced_samples(scope_count: int = 4) -> list:
    samples = []
    movements = (-1.2, -0.8, -0.4, 0.4, 0.8, 1.2)
    for scope_index in range(scope_count):
        for candidate_index, movement in enumerate(movements):
            success = movement > 0.0
            samples.append(
                ReplayRankingSample(
                    sample_id=f"scope{scope_index}-candidate{candidate_index}",
                    scope_id=f"outer-incumbent-{scope_index}",
                    parameter_log_delta={
                        "seq_page_cost": movement,
                        "cpu_tuple_cost": -0.25 * movement,
                    },
                    db_qps_delta_pct=(2.0 + movement if success else -2.0 + movement),
                    target_util_delta_pp=(-1.0 - movement if success else 1.0 - movement),
                    scenario_features={
                        "workload": "tpch",
                        "target_resource": "memory",
                        "cpu_cores": 8.0 + scope_index,
                    },
                    plan_features={
                        "plan_family": "beneficial" if success else "harmful",
                        "changed_plan_count": 2 if success else 4,
                    },
                )
            )
    return samples


def test_config() -> DirectionRankerConfig:
    return DirectionRankerConfig(
        min_samples=12,
        min_scopes=3,
        min_positive_per_task=3,
        min_negative_per_task=3,
        max_group_folds=4,
    )


class RealReplayDirectionRankerTests(unittest.TestCase):
    def test_group_holdout_metrics_and_deployable_ranking(self) -> None:
        samples = balanced_samples()
        result = train_real_replay_direction_ranker(samples, config=test_config())

        self.assertTrue(result.ready, result.fail_closed_reasons)
        self.assertIsNotNone(result.model)
        self.assertEqual(len(result.heldout_predictions), len(samples))
        self.assertFalse(result.heldout_metrics["scope_leakage_detected"])
        self.assertEqual(result.heldout_metrics["group_holdout_prediction_count"], 24)

        for fold in result.fold_audit:
            self.assertFalse(
                set(fold["train_scope_ids"]).intersection(fold["heldout_scope_ids"])
            )
            self.assertEqual(fold["scope_overlap"], [])

        for task in (TASK_QPS_UP, TASK_TARGET_DOWN, TASK_JOINT):
            metrics = result.heldout_metrics["tasks"][task]
            self.assertEqual(metrics["sample_count"], 24)
            self.assertGreater(metrics["accuracy_wilson_lower"], 0.0)
            self.assertGreater(metrics["precision_wilson_lower"], 0.0)
            self.assertGreater(metrics["recall_wilson_lower"], 0.0)
            ranking = metrics["ranking"]
            self.assertEqual(
                ranking["comparison_scope"],
                "within_same_heldout_outer_incumbent_only",
            )
            self.assertEqual(ranking["evaluable_pair_scope_count"], 4)
            self.assertGreaterEqual(ranking["pairwise_auc_micro"], 0.99)
            self.assertLessEqual(
                ranking["pairwise_inversion_cluster_bootstrap_upper"],
                0.10,
            )
            self.assertGreater(ranking["top1_success_wilson_lower"], 0.0)

        # Prediction candidates intentionally have no real outcome fields.
        candidates = [
            ReplayRankingCandidate(
                sample_id="future-good",
                scope_id="future-incumbent",
                parameter_log_delta={"seq_page_cost": 0.9, "cpu_tuple_cost": -0.2},
                scenario_features={
                    "workload": "tpch",
                    "target_resource": "memory",
                    "cpu_cores": 16.0,
                },
                plan_features={
                    "plan_family": "beneficial",
                    "changed_plan_count": 2,
                },
            ),
            ReplayRankingCandidate(
                sample_id="future-bad",
                scope_id="future-incumbent",
                parameter_log_delta={"seq_page_cost": -0.9, "cpu_tuple_cost": 0.2},
                scenario_features={
                    "workload": "unseen_workload",
                    "target_resource": "memory",
                },
                plan_features={"plan_family": "unseen_plan_family"},
            ),
        ]
        ranked = result.model.rank(candidates)
        self.assertEqual(ranked[0]["sample_id"], "future-good")
        self.assertGreater(ranked[0]["ranking_score"], ranked[1]["ranking_score"])

        artifact = training_result_to_json(result)
        parsed = json.loads(artifact)
        self.assertTrue(parsed["ready"])
        self.assertEqual(
            parsed["model"]["ranking_score"],
            "joint_direction_success_probability",
        )
        restored = direction_ranker_from_dict(parsed["model"])
        restored_ranked = restored.rank(candidates)
        self.assertEqual(
            [item["sample_id"] for item in restored_ranked],
            [item["sample_id"] for item in ranked],
        )

    def test_no_joint_positive_fails_closed(self) -> None:
        samples = []
        for scope_index in range(4):
            for candidate_index, movement in enumerate((-1.0, -0.5, 0.5, 1.0)):
                samples.append(
                    ReplayRankingSample(
                        sample_id=f"n{scope_index}-{candidate_index}",
                        scope_id=f"n-scope-{scope_index}",
                        parameter_log_delta={"p": movement},
                        # QPS and target each sometimes succeed, but never jointly.
                        db_qps_delta_pct=1.0 if movement > 0.0 else -1.0,
                        target_util_delta_pp=1.0 if movement > 0.0 else -1.0,
                    )
                )
        result = train_real_replay_direction_ranker(samples, config=test_config())
        self.assertFalse(result.ready)
        self.assertIsNone(result.model)
        self.assertTrue(
            any(
                "joint_direction_success_has_no_positive" in reason
                for reason in result.fail_closed_reasons
            )
        )

    def test_insufficient_outer_scopes_fails_closed(self) -> None:
        result = train_real_replay_direction_ranker(
            balanced_samples(scope_count=2),
            config=test_config(),
        )
        self.assertFalse(result.ready)
        self.assertIsNone(result.model)
        self.assertIn("insufficient_scopes", result.reason)

    def test_fold_without_positive_training_support_fails_closed(self) -> None:
        samples = []
        # Global support is adequate, but every positive is isolated in one
        # scope.  Holding that scope out leaves a no-positive training fold.
        for scope_index in range(3):
            for candidate_index in range(6):
                positive = scope_index == 0 and candidate_index < 3
                movement = 0.5 + candidate_index / 10.0
                samples.append(
                    ReplayRankingSample(
                        sample_id=f"f{scope_index}-{candidate_index}",
                        scope_id=f"f-scope-{scope_index}",
                        parameter_log_delta={"p": movement},
                        db_qps_delta_pct=1.0 if positive else -1.0,
                        target_util_delta_pp=-1.0 if positive else 1.0,
                    )
                )
        result = train_real_replay_direction_ranker(samples, config=test_config())
        self.assertFalse(result.ready)
        self.assertIsNone(result.model)
        self.assertTrue(
            any(
                "train_joint_direction_success_has_no_positive" in reason
                for reason in result.fail_closed_reasons
            )
        )

    def test_no_within_scope_ranking_pairs_fails_closed(self) -> None:
        samples = []
        # Each scope is class-pure.  Cross-scope classification is possible,
        # but no held-out incumbent contains a positive/negative candidate
        # pair, so a within-incumbent ranking claim is unsupported.
        for scope_index in range(4):
            positive_scope = scope_index < 2
            for candidate_index in range(4):
                movement = (0.5 + candidate_index / 10.0) * (
                    1.0 if positive_scope else -1.0
                )
                samples.append(
                    ReplayRankingSample(
                        sample_id=f"pure{scope_index}-{candidate_index}",
                        scope_id=f"pure-scope-{scope_index}",
                        parameter_log_delta={"p": movement},
                        db_qps_delta_pct=1.0 if positive_scope else -1.0,
                        target_util_delta_pp=-1.0 if positive_scope else 1.0,
                    )
                )
        result = train_real_replay_direction_ranker(samples, config=test_config())
        self.assertFalse(result.ready)
        self.assertIsNone(result.model)
        self.assertIn("insufficient_heldout_joint_ranking_scopes", result.reason)
        ranking = result.heldout_metrics["tasks"][TASK_JOINT]["ranking"]
        self.assertEqual(ranking["evaluable_pair_scope_count"], 0)

    def test_deployment_ranking_threshold_fails_closed(self) -> None:
        config = DirectionRankerConfig(
            min_samples=12,
            min_scopes=3,
            min_positive_per_task=3,
            min_negative_per_task=3,
            max_group_folds=4,
            min_joint_pair_count=10_000,
        )
        result = train_real_replay_direction_ranker(
            balanced_samples(),
            config=config,
        )
        self.assertFalse(result.ready)
        self.assertIsNone(result.model)
        self.assertTrue(
            any(
                "heldout_joint_pair_count_below_threshold" in reason
                for reason in result.fail_closed_reasons
            )
        )
        self.assertFalse(result.heldout_metrics["deployment_admission"]["passed"])

    def test_duplicate_sample_ids_are_rejected(self) -> None:
        sample = balanced_samples()[0]
        with self.assertRaisesRegex(ValueError, "sample_id values must be unique"):
            train_real_replay_direction_ranker([sample, sample], config=test_config())


def main() -> None:
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(
        RealReplayDirectionRankerTests
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    main()
