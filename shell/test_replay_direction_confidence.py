#!/usr/bin/env python3
"""Tests for conservative real-replay direction confidence calibration."""

from __future__ import annotations

import math
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from replay_direction_confidence import (  # noqa: E402
    ACTION_ALLOW,
    ACTION_FORBID,
    ACTION_REVERSE,
    ACTION_REVERSE_PROBE,
    DirectionConfidenceConfig,
    ReplayDirectionSample,
    calibrate_replay_directions,
    calibrate_vector_failure_recovery,
)


def sample(
    delta: float,
    qps: float,
    target: float,
    index: int,
    *,
    other_delta: float = 0.0,
) -> ReplayDirectionSample:
    deltas = {"seq_page_cost": delta}
    if other_delta != 0.0:
        deltas["cpu_tuple_cost"] = other_delta
    return ReplayDirectionSample(
        parameter_log_delta=deltas,
        db_qps_delta_pct=qps,
        target_util_delta_pp=target,
        sample_id=f"r{index}",
    )


class ReplayDirectionConfidenceTests(unittest.TestCase):
    def test_allows_proposed_direction_with_confident_joint_sign(self) -> None:
        samples = [sample(0.10, 2.0 + i, -1.0, i) for i in range(5)]
        decision = calibrate_replay_directions(
            samples,
            {"seq_page_cost": 1},
        )["seq_page_cost"]
        self.assertEqual(decision.action, ACTION_ALLOW)
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.recommended_direction, 1)
        self.assertEqual(decision.proposed_evidence.joint_success_count, 5)
        self.assertGreaterEqual(
            decision.proposed_evidence.joint_success_wilson_lower,
            0.60,
        )

    def test_reverses_only_when_opposite_direction_is_confident(self) -> None:
        samples = [
            sample(0.10, -2.0, 1.0, i)
            for i in range(4)
        ] + [
            sample(-0.10, 3.0, -2.0, i + 4)
            for i in range(5)
        ]
        decision = calibrate_replay_directions(
            samples,
            {"seq_page_cost": 1},
        )["seq_page_cost"]
        self.assertEqual(decision.action, ACTION_REVERSE)
        self.assertEqual(decision.recommended_direction, -1)
        self.assertFalse(decision.proposed_evidence.supported)
        self.assertTrue(decision.opposite_evidence.supported)

    def test_sparse_successes_forbid_strong_direction(self) -> None:
        samples = [sample(0.10, 4.0, -2.0, i) for i in range(3)]
        decision = calibrate_replay_directions(
            samples,
            {"seq_page_cost": 1},
        )["seq_page_cost"]
        self.assertEqual(decision.action, ACTION_FORBID)
        self.assertEqual(decision.recommended_direction, 0)
        self.assertIn(
            "insufficient_samples",
            decision.proposed_evidence.support_failures,
        )

    def test_mixed_joint_sign_evidence_is_not_confident(self) -> None:
        samples = [sample(0.10, 3.0, -1.0, i) for i in range(4)]
        samples.extend(
            [
                sample(0.10, -1.0, -1.0, 4),
                sample(0.10, 3.0, 1.0, 5),
            ]
        )
        decision = calibrate_replay_directions(
            samples,
            {"seq_page_cost": 1},
        )["seq_page_cost"]
        self.assertEqual(decision.action, ACTION_FORBID)
        self.assertIn(
            "joint_confidence_below_threshold",
            decision.proposed_evidence.support_failures,
        )

    def test_confounded_multi_parameter_replays_are_ignored_by_default(self) -> None:
        samples = [
            sample(0.10, 5.0, -2.0, i, other_delta=0.10)
            for i in range(5)
        ]
        decision = calibrate_replay_directions(
            samples,
            {"seq_page_cost": 1},
        )["seq_page_cost"]
        self.assertEqual(decision.action, ACTION_FORBID)
        self.assertEqual(decision.ignored_sample_count, 5)
        self.assertEqual(
            decision.ignored_reason_counts["confounded_low_focal_share"],
            5,
        )

        relaxed = calibrate_replay_directions(
            samples,
            {"seq_page_cost": 1},
            config=DirectionConfidenceConfig(min_focal_movement_share=0.50),
        )["seq_page_cost"]
        self.assertEqual(relaxed.action, ACTION_ALLOW)

    def test_zero_effect_does_not_count_as_joint_success(self) -> None:
        samples = [sample(-0.10, 0.0, 0.0, i) for i in range(5)]
        decision = calibrate_replay_directions(
            samples,
            {"seq_page_cost": -1},
        )["seq_page_cost"]
        self.assertEqual(decision.action, ACTION_FORBID)
        self.assertEqual(decision.proposed_evidence.joint_success_count, 0)

    def test_nonfinite_evidence_and_invalid_direction_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "db_qps_delta_pct must be finite"):
            calibrate_replay_directions(
                [sample(0.1, math.nan, -1.0, 1)],
                {"seq_page_cost": 1},
            )
        with self.assertRaisesRegex(ValueError, r"must be -1 or \+1"):
            calibrate_replay_directions(
                [sample(0.1, 1.0, -1.0, 1)],
                {"seq_page_cost": 0},
            )
        duplicate = sample(0.1, 1.0, -1.0, 1)
        with self.assertRaisesRegex(ValueError, "sample_id values must be unique"):
            calibrate_replay_directions(
                [duplicate, duplicate],
                {"seq_page_cost": 1},
            )


class VectorFailureRecoveryTests(unittest.TestCase):
    @staticmethod
    def vector_sample(
        first_delta: float,
        second_delta: float,
        qps: float,
        target: float,
        index: int,
    ) -> ReplayDirectionSample:
        return ReplayDirectionSample(
            parameter_log_delta={
                "seq_page_cost": first_delta,
                "cpu_tuple_cost": second_delta,
            },
            db_qps_delta_pct=qps,
            target_util_delta_pp=target,
            sample_id=f"v{index}",
        )

    def test_high_alignment_repeated_failure_triggers_reverse_probe(self) -> None:
        samples = [
            self.vector_sample(0.10, -0.10, -1.0, -2.0, 1),
            self.vector_sample(0.12, -0.11, 2.0, 0.0, 2),
            self.vector_sample(0.09, -0.10, 0.0, -1.0, 3),
            self.vector_sample(0.11, -0.12, 1.0, 0.5, 4),
            self.vector_sample(0.10, -0.09, -0.5, 1.0, 5),
        ]
        decision = calibrate_vector_failure_recovery(
            samples,
            {"seq_page_cost": 1.0, "cpu_tuple_cost": -1.0},
        )
        self.assertEqual(decision.action, ACTION_REVERSE_PROBE)
        self.assertTrue(decision.probe_only)
        self.assertFalse(decision.acceptance_proven)
        self.assertEqual(decision.aligned_sample_count, 5)
        self.assertEqual(decision.joint_failure_count, 5)
        self.assertGreaterEqual(
            decision.joint_failure_wilson_lower,
            decision.min_failure_wilson_lower_bound,
        )
        self.assertEqual(
            decision.reverse_probe_direction_vector,
            {"seq_page_cost": -1.0, "cpu_tuple_cost": 1.0},
        )
        self.assertGreaterEqual(decision.eta_multiplier, 0.10)
        self.assertLessEqual(decision.eta_multiplier, 0.25)

    def test_mixed_and_reverse_vectors_do_not_trigger(self) -> None:
        samples = [
            self.vector_sample(0.10, 0.10, -1.0, 1.0, 1),
            self.vector_sample(-0.10, 0.10, -1.0, 1.0, 2),
            self.vector_sample(0.10, 0.08, -1.0, 1.0, 3),
            self.vector_sample(-0.12, 0.11, -1.0, 1.0, 4),
            self.vector_sample(0.08, 0.10, -1.0, 1.0, 5),
        ]
        decision = calibrate_vector_failure_recovery(
            samples,
            {"seq_page_cost": 1.0, "cpu_tuple_cost": -1.0},
        )
        self.assertEqual(decision.action, ACTION_FORBID)
        self.assertEqual(decision.aligned_sample_count, 0)
        self.assertEqual(decision.ignored_low_alignment_count, 5)
        self.assertEqual(decision.reverse_probe_direction_vector, {})
        self.assertEqual(decision.eta_multiplier, 0.0)

    def test_aligned_successes_and_sparse_failures_do_not_trigger(self) -> None:
        direction = {"seq_page_cost": 1.0, "cpu_tuple_cost": -1.0}
        successes = [
            self.vector_sample(0.10, -0.10, 2.0, -1.0, index)
            for index in range(1, 6)
        ]
        success_decision = calibrate_vector_failure_recovery(
            successes,
            direction,
        )
        self.assertEqual(success_decision.action, ACTION_FORBID)
        self.assertEqual(success_decision.aligned_sample_count, 5)
        self.assertEqual(success_decision.joint_failure_count, 0)

        sparse_failures = [
            self.vector_sample(0.10, -0.10, -1.0, 1.0, index)
            for index in range(1, 4)
        ]
        sparse_decision = calibrate_vector_failure_recovery(
            sparse_failures,
            direction,
        )
        self.assertEqual(sparse_decision.action, ACTION_FORBID)
        self.assertEqual(sparse_decision.aligned_sample_count, 3)

    def test_vector_recovery_rejects_nonfinite_and_zero_vectors(self) -> None:
        valid_sample = self.vector_sample(0.10, -0.10, -1.0, 1.0, 1)
        with self.assertRaisesRegex(ValueError, "must be finite"):
            calibrate_vector_failure_recovery(
                [valid_sample],
                {"seq_page_cost": math.nan, "cpu_tuple_cost": -1.0},
            )
        with self.assertRaisesRegex(ValueError, "non-zero finite norm"):
            calibrate_vector_failure_recovery(
                [valid_sample],
                {"seq_page_cost": 0.0, "cpu_tuple_cost": 0.0},
            )
        zero_sample = self.vector_sample(0.0, 0.0, -1.0, 1.0, 2)
        with self.assertRaisesRegex(ValueError, "non-zero finite norm"):
            calibrate_vector_failure_recovery(
                [zero_sample],
                {"seq_page_cost": 1.0, "cpu_tuple_cost": -1.0},
            )


if __name__ == "__main__":
    unittest.main()
