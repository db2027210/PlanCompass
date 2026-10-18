from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import unittest

from formal_acceptance_policy import LivePoint
from formal_baseline_gates import PressureEvidence, evaluate_default_baseline


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "preflight/frozen_formal_config_20260721_v3/measurement_protocol.json"


def point() -> LivePoint:
    return LivePoint(
        correctness_passed=True,
        stable=True,
        sample_coverage=0.99,
        error_rate=0.0,
        oom=False,
        database_restarted=False,
        qps=10.0,
        xap_qps=None,
        xtp_tps=None,
        latency_p95_ms=10.0,
        latency_p99_ms=20.0,
        cpu_utilization=0.5,
        memory_utilization=0.9,
        io_utilization=0.4,
    )


class BaselineGateTests(unittest.TestCase):
    def test_memory_requires_active_pressure(self) -> None:
        failed = evaluate_default_baseline(
            PROTOCOL, point(), PressureEvidence(), scenario="memory", xp=False
        )
        self.assertFalse(failed.passed)
        self.assertIn("memory_pressure_evidence_absent", failed.failures)
        passed = evaluate_default_baseline(
            PROTOCOL,
            point(),
            PressureEvidence(working_set_refaults_observed=True),
            scenario="memory",
            xp=False,
        )
        self.assertTrue(passed.passed)

    def test_io_requires_xp_progress_and_invariant_runtime(self) -> None:
        xp_point = replace(
            point(),
            qps=None,
            xap_qps=1.0,
            xtp_tps=10.0,
            memory_utilization=0.6,
            io_utilization=0.9,
        )
        passed = evaluate_default_baseline(
            PROTOCOL,
            xp_point,
            PressureEvidence(
                io_throttle_or_wait_observed=True,
                xap_completed=1,
                at_transactions_completed=1,
            ),
            scenario="io",
            xp=True,
        )
        self.assertTrue(passed.passed)

    def test_none_rejects_any_resource_above_limit(self) -> None:
        failed = evaluate_default_baseline(
            PROTOCOL,
            replace(point(), memory_utilization=0.76),
            PressureEvidence(),
            scenario="none",
            xp=False,
        )
        self.assertFalse(failed.passed)
        self.assertIn("none_resource_utilization_above_maximum", failed.failures)


if __name__ == "__main__":
    unittest.main(verbosity=2)
