from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tools import resume_xp_fresh0_fixed_contract_target10_v34 as v34


class FakeStore:
    def __init__(self) -> None:
        self.value = {"accepted_count": 0, "gradient_state": {"update_count": 0}}

    def load(self) -> dict:
        return copy.deepcopy(self.value)


class PreR0RestoreTests(unittest.TestCase):
    def test_restore_precedes_postgres_verification_and_is_state_neutral(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            restore = root / "restore.sh"
            restore.write_text("#!/bin/sh\n", encoding="utf-8")
            config = root / "config.json"
            config.write_text("{}\n", encoding="utf-8")
            store = FakeStore()
            events = []

            def run(*args, **kwargs):  # type: ignore[no-untyped-def]
                events.append("restore")
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            def verify(config_value):  # type: ignore[no-untyped-def]
                events.append("verify")
                return {
                    "database": "hybench_sf10x",
                    "seven_parameter_baseline": copy.deepcopy(
                        v34.EXPECTED_BASELINE
                    ),
                }

            with (
                mock.patch.object(v34, "_assert_affinity"),
                mock.patch.object(v34, "_authority_paths", return_value=[]),
                mock.patch.object(v34, "_sealed_hashes", return_value={}),
                mock.patch.object(v34.subprocess, "run", side_effect=run),
                mock.patch.object(v34, "_verify_postgres", side_effect=verify),
            ):
                audit = v34.restore_and_verify_before_r0_builder(
                    campaign=root / "campaign",
                    state_store=store,
                    config=config,
                    restore_script=restore,
                )
            self.assertEqual(events, ["restore", "verify"])
            self.assertEqual(audit["physical_workload_calls_added"], 0)
            self.assertEqual(
                audit["campaign_state_sha256_before"],
                audit["campaign_state_sha256_after"],
            )

    def test_existing_sealed_restore_audit_never_runs_restore_again(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            campaign = root / "campaign"
            campaign.mkdir()
            restore = root / "restore.sh"
            restore.write_text("#!/bin/sh\n", encoding="utf-8")
            config = root / "config.json"
            config.write_text("{}\n", encoding="utf-8")
            store = FakeStore()
            digest = v34._canonical_sha256(store.load())
            audit = {
                "status": "complete",
                "restore_script_sha256": v34.v33.sha256(restore),
                "authority_sha256_before": {},
                "authority_sha256_after": {},
                "campaign_state_sha256_before": digest,
                "campaign_state_sha256_after": digest,
            }
            (campaign / "v34_pre_r0_restore_audit.json").write_text(
                json.dumps(audit),
                encoding="utf-8",
            )
            with (
                mock.patch.object(v34, "_assert_affinity"),
                mock.patch.object(v34, "_authority_paths", return_value=[]),
                mock.patch.object(v34, "_sealed_hashes", return_value={}),
                mock.patch.object(v34, "_verify_postgres"),
                mock.patch.object(v34.subprocess, "run") as run,
            ):
                observed = v34.restore_and_verify_before_r0_builder(
                    campaign=campaign,
                    state_store=store,
                    config=config,
                    restore_script=restore,
                )
            run.assert_not_called()
            self.assertEqual(observed["status"], "complete")

    def test_wrapper_orders_restore_before_r0_builder(self) -> None:
        events = []
        kwargs = {
            "state_store": FakeStore(),
            "campaign": Path("/sealed/campaign"),
            "config": Path("/sealed/config.json"),
            "restore_script": Path("/sealed/restore.sh"),
        }
        with (
            mock.patch.object(
                v34,
                "restore_and_verify_before_r0_builder",
                side_effect=lambda **unused: events.append("restore"),
            ),
            mock.patch.object(
                v34,
                "_BASE_BOOTSTRAP",
                side_effect=lambda **unused: events.append("builder") or {},
            ),
        ):
            v34.run_fresh_runtime_baseline_bootstrap_v34(**kwargs)
        self.assertEqual(events, ["restore", "builder"])

    def test_v33_partial_is_quarantined_without_inheritance(self) -> None:
        audit_path = Path(
            "/home/anonymous/program/resource_adaptive/history/"
            "xp_v33_partial_r0_builder_technical_audit_20260803.json"
        )
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        self.assertEqual(audit["physical_workload_calls"], 0)
        self.assertEqual(audit["valid_replays"], 0)
        self.assertEqual(audit["technical_failures"], 1)
        self.assertFalse(audit["formal_state_inheritance_allowed"])
        self.assertFalse(audit["replay_or_score_training_eligible"])


if __name__ == "__main__":
    unittest.main()
