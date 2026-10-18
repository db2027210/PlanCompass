from __future__ import annotations

from pathlib import Path
import os
import tempfile
import unittest
from unittest import mock

from shell import run_mysql_sgd_direct_campaign as direct


class CandidateFailureClassificationTest(unittest.TestCase):
    def test_direct_host02_campaigns_skip_cross_scenario_lease(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            lock_path = Path(raw) / "host02.lock"
            with mock.patch.object(direct, "HOST02_CAMPAIGN_LOCK", lock_path):
                self.assertIsNone(
                    direct.acquire_direct_host02_lease("job_memory")
                )
                self.assertIsNone(
                    direct.acquire_direct_host02_lease("hybench_xp_io")
                )
                self.assertFalse(lock_path.exists())

    def test_controller_lease_marker_avoids_self_deadlock(self) -> None:
        with mock.patch.dict(
            os.environ, {"MYSQL_HOST02_CAMPAIGN_LEASE_HELD": "1"}
        ):
            self.assertIsNone(
                direct.acquire_direct_host02_lease("hybench_xp_io")
            )

    def test_correctness_failure_is_a_valid_candidate_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            point = Path(raw)
            (point / "failure.txt").write_text(
                "RuntimeError: FormalJobSessionError: JOB correctness failed: "
                "{'missing_queries': ['03_job_9a.sql']}\n",
                encoding="utf-8",
            )

            failure = direct.classify_candidate_failure(point)

        self.assertIsNotNone(failure)
        assert failure is not None
        self.assertEqual(failure["category"], "formal_correctness_gate_failed")
        self.assertIn("03_job_9a.sql", failure["message"])

    def test_infrastructure_failure_remains_a_technical_retry(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            point = Path(raw)
            (point / "failure.txt").write_text(
                "RuntimeError: SSH tunnel exited before readiness\n",
                encoding="utf-8",
            )

            failure = direct.classify_candidate_failure(point)

        self.assertIsNone(failure)

    def test_missing_failure_artifact_remains_a_technical_retry(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            failure = direct.classify_candidate_failure(Path(raw))

        self.assertIsNone(failure)

    def test_resume_revalidates_decisions_and_ignores_unproven_counter(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            campaign = root / "direct_20260724T010000"
            output = root / "direct_20260724T020000"
            campaign.mkdir()
            output.mkdir()
            direct.atomic_json(campaign / "round_005.costs.json", {"cost": 2.0})
            direct.atomic_json(
                campaign / "round_005.decision.json",
                {
                    "accepted": True,
                    "incumbent_before": {
                        "db_qps": 0.5,
                        "resource_means": {"cpu": 0.3, "memory": 0.9, "io": 0.2},
                    },
                    "candidate": {
                        "db_qps": 1.0,
                        "resource_means": {"cpu": 0.4, "memory": 0.8, "io": 0.3},
                    },
                },
            )
            direct.atomic_json(
                campaign / "checkpoint.json",
                {"accepted_shrinkage": 3},
            )

            _, _, accepted, resumed_from = direct.resume_checkpoint(
                root,
                output,
                {"cost": 1.0},
                {
                    "db_qps": 0.5,
                    "resource_means": {"cpu": 0.3, "memory": 0.9, "io": 0.2},
                },
                "memory",
            )

        self.assertEqual(accepted, 1)
        self.assertIsNotNone(resumed_from)
        assert resumed_from is not None
        self.assertEqual(resumed_from["accepted_shrinkage"], 1)
        self.assertFalse(resumed_from["checkpoint_counter_trusted_without_decisions"])

    def test_resume_rejects_old_incumbent_with_non_target_bottleneck(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            campaign = root / "direct_20260724T010000"
            output = root / "direct_20260724T020000"
            campaign.mkdir()
            output.mkdir()
            direct.atomic_json(campaign / "round_001.costs.json", {"cost": 2.0})
            direct.atomic_json(
                campaign / "round_001.decision.json",
                {
                    "accepted": True,
                    "incumbent_before": {
                        "db_qps": 1.0,
                        "resource_means": {"cpu": 0.3, "memory": 0.9, "io": 0.2},
                    },
                    "candidate": {
                        "db_qps": 1.1,
                        "resource_means": {"cpu": 0.8, "memory": 0.85, "io": 0.3},
                    },
                },
            )
            later = root / "direct_20260724T015000"
            later.mkdir()
            direct.atomic_json(later / "round_001.costs.json", {"cost": 3.0})
            direct.atomic_json(
                later / "round_001.decision.json",
                {
                    "accepted": True,
                    "incumbent_before": {
                        "db_qps": 1.1,
                        "resource_means": {"cpu": 0.4, "memory": 0.85, "io": 0.3},
                    },
                    "candidate": {
                        "db_qps": 1.2,
                        "resource_means": {"cpu": 0.5, "memory": 0.8, "io": 0.4},
                    },
                },
            )

            _, _, accepted, resumed_from = direct.resume_checkpoint(
                root,
                output,
                {"cost": 1.0},
                {
                    "db_qps": 1.0,
                    "resource_means": {"cpu": 0.3, "memory": 0.9, "io": 0.2},
                },
                "memory",
            )

        self.assertEqual(accepted, 0)
        self.assertIsNone(resumed_from)


if __name__ == "__main__":
    unittest.main()
