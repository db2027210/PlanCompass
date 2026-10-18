from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import unittest

from formal_acceptance_policy import (
    EligibleFinalPoint,
    FrozenAcceptanceError,
    LivePoint,
    decide_outer_candidate,
    select_final_point,
)


ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "preflight" / "frozen_formal_config_20260721_v3" / "acceptance_policy.json"


def normal_point() -> LivePoint:
    return LivePoint(
        correctness_passed=True,
        stable=True,
        sample_coverage=0.99,
        error_rate=0.0,
        oom=False,
        database_restarted=False,
        qps=100.0,
        xap_qps=None,
        xtp_tps=None,
        latency_p95_ms=10.0,
        latency_p99_ms=20.0,
        cpu_utilization=0.50,
        memory_utilization=0.90,
        io_utilization=0.40,
    )


class FrozenAcceptancePolicyTests(unittest.TestCase):
    def test_memory_throughput_branch_passes_without_plan_change(self) -> None:
        incumbent = normal_point()
        candidate = replace(incumbent, qps=102.0, memory_utilization=0.905)
        decision = decide_outer_candidate(
            POLICY, incumbent, candidate, bottleneck="memory", xp=False, plan_changed=False
        )
        self.assertTrue(decision.accepted)
        self.assertTrue(decision.branch_throughput_passed)
        self.assertFalse(decision.plan_changed)

    def test_memory_resource_branch_allows_registered_throughput_loss(self) -> None:
        incumbent = normal_point()
        candidate = replace(incumbent, qps=98.0, memory_utilization=0.84)
        decision = decide_outer_candidate(
            POLICY, incumbent, candidate, bottleneck="memory", xp=False, plan_changed=True
        )
        self.assertTrue(decision.accepted)
        self.assertTrue(decision.branch_resource_passed)

    def test_hard_latency_gate_rejects_even_with_throughput_gain(self) -> None:
        incumbent = normal_point()
        candidate = replace(incumbent, qps=120.0, latency_p99_ms=23.0)
        decision = decide_outer_candidate(
            POLICY, incumbent, candidate, bottleneck="memory", xp=False, plan_changed=True
        )
        self.assertFalse(decision.accepted)
        self.assertIn("p99_regression_above_maximum", decision.hard_gate_failures)

    def test_none_requires_low_resources_and_positive_throughput(self) -> None:
        incumbent = replace(normal_point(), memory_utilization=0.60)
        passing = replace(incumbent, qps=102.0)
        self.assertTrue(
            decide_outer_candidate(
                POLICY, incumbent, passing, bottleneck="none", xp=False, plan_changed=False
            ).accepted
        )
        failing = replace(passing, cpu_utilization=0.76)
        self.assertFalse(
            decide_outer_candidate(
                POLICY, incumbent, failing, bottleneck="none", xp=False, plan_changed=False
            ).accepted
        )

    def test_xp_uses_both_streams_and_enforces_each_stream_floor(self) -> None:
        incumbent = replace(normal_point(), qps=None, xap_qps=100.0, xtp_tps=100.0)
        candidate = replace(incumbent, xap_qps=94.0, xtp_tps=125.0, io_utilization=0.36)
        decision = decide_outer_candidate(
            POLICY, incumbent, candidate, bottleneck="io", xp=True, plan_changed=True
        )
        self.assertFalse(decision.accepted)
        self.assertIn("xap_regression_above_maximum", decision.hard_gate_failures)

    def test_invalid_ratio_units_fail_closed(self) -> None:
        with self.assertRaises(FrozenAcceptanceError):
            decide_outer_candidate(
                POLICY,
                normal_point(),
                replace(normal_point(), sample_coverage=99.0),
                bottleneck="memory",
                xp=False,
                plan_changed=False,
            )

    def test_final_selection_uses_frozen_ranking(self) -> None:
        default = normal_point()
        earlier = replace(default, qps=105.0, memory_utilization=0.80)
        later = replace(default, qps=104.0, memory_utilization=0.80)
        selection = select_final_point(
            POLICY,
            default,
            [
                EligibleFinalPoint("candidate_1", 1, earlier),
                EligibleFinalPoint("candidate_2", 2, later),
            ],
            bottleneck="memory",
            xp=False,
        )
        self.assertEqual(selection.label, "candidate_1")
        self.assertEqual(selection.candidate_index, 1)
        self.assertEqual(selection.eligible_point_count, 3)

    def test_final_selection_can_keep_default(self) -> None:
        default = replace(normal_point(), memory_utilization=0.60)
        worse = replace(default, qps=99.0)
        selection = select_final_point(
            POLICY,
            default,
            [EligibleFinalPoint("candidate_1", 1, worse)],
            bottleneck="none",
            xp=False,
        )
        self.assertEqual(selection.label, "default")

    def test_non_xp_rejects_xp_stream_fields(self) -> None:
        with self.assertRaises(FrozenAcceptanceError):
            decide_outer_candidate(
                POLICY,
                normal_point(),
                replace(normal_point(), xap_qps=1.0),
                bottleneck="memory",
                xp=False,
                plan_changed=False,
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
