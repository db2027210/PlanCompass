from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import resume_xp_fresh0_fixed_contract_target10_v35 as v35


class FakeStore:
    def __init__(self) -> None:
        self.value = {"accepted_count": 0, "gradient_state": {"update_count": 0}}

    def load(self) -> dict:
        return copy.deepcopy(self.value)


class AuthoritativeFormalRootTests(unittest.TestCase):
    def test_postgres_retry_recovers_after_two_transient_failures(self) -> None:
        calls = []
        sleeps = []

        def verify(config: dict) -> dict:
            calls.append(copy.deepcopy(config))
            if len(calls) < 3:
                raise ConnectionError(f"transient-{len(calls)}")
            return {"database": "hybench_sf10x"}

        config = {
            "postgres": {
                "dbname": "hybench_sf10x",
                "host": "127.0.0.1",
                "port": 59630,
                "user": "postgres",
            }
        }
        original = copy.deepcopy(config)
        result = v35.verify_postgres_with_retry(
            config,
            attempts=4,
            delay_s=0.25,
            verify_func=verify,
            sleep_func=sleeps.append,
        )
        self.assertEqual(result["postgres"]["database"], "hybench_sf10x")
        self.assertEqual(result["attempt_count"], 3)
        self.assertEqual(result["failure_count"], 2)
        self.assertEqual(sleeps, [0.25, 0.25])
        self.assertEqual(config, original)

    def test_postgres_retry_exhaustion_has_safe_diagnostics(self) -> None:
        config = {
            "postgres": {
                "dbname": "hybench_sf10x",
                "host": "127.0.0.1",
                "port": 59630,
                "user": "postgres",
                "password": "CHANGE_ME",
            }
        }
        original = copy.deepcopy(config)

        def verify(_config: dict) -> dict:
            raise ConnectionError("refused")

        with self.assertRaisesRegex(
            RuntimeError,
            r'"attempt_count": 3.*"exception_type": "ConnectionError"',
        ) as captured:
            v35.verify_postgres_with_retry(
                config,
                attempts=3,
                delay_s=0,
                verify_func=verify,
                sleep_func=lambda _delay: None,
            )
        self.assertNotIn("must-not-leak", str(captured.exception))
        self.assertEqual(config, original)

    def test_only_r0_builder_formal_root_is_rewritten(self) -> None:
        bundle = Path("/sealed/bundle")
        formal = bundle / "authoritative_round9"
        builder = [
            "python",
            "/sealed/build_xp_incumbent_revalidation_candidate.py",
            "--formal-root",
            str(bundle),
            "--output",
            "/sealed/out.json",
        ]
        rewritten = v35._rewrite_builder_formal_root(
            builder,
            bundle=bundle,
            formal_root=formal,
        )
        self.assertEqual(
            rewritten[rewritten.index("--formal-root") + 1],
            str(formal),
        )
        runner = ["python", "/sealed/run_xp_incumbent_revalidation_once.py"]
        self.assertEqual(
            v35._rewrite_builder_formal_root(
                runner,
                bundle=bundle,
                formal_root=formal,
            ),
            runner,
        )

    def test_unexpected_builder_root_fails_closed(self) -> None:
        command = [
            "python",
            "/sealed/build_xp_incumbent_revalidation_candidate.py",
            "--formal-root",
            "/wrong/root",
        ]
        with self.assertRaisesRegex(RuntimeError, "source mismatch"):
            v35._rewrite_builder_formal_root(
                command,
                bundle=Path("/sealed/bundle"),
                formal_root=Path("/sealed/bundle/authoritative_round9"),
            )

    def test_reuse_audit_does_not_execute_restore(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            campaign = root / "campaign"
            campaign.mkdir()
            restore = root / "restore.sh"
            restore.write_text("#!/bin/sh\n", encoding="utf-8")
            config = root / "config.json"
            config.write_text("{}\n", encoding="utf-8")
            source = root / "source.json"
            authority = {"/sealed/formal": "sha"}
            source.write_text(
                json.dumps(
                    {
                        "status": "complete",
                        "physical_workload_calls_added": 0,
                        "cpuset": v35.v34.EXPECTED_CPUSET_TEXT,
                        "restore_script_sha256": v35.v33.sha256(restore),
                        "authority_sha256_before": authority,
                        "authority_sha256_after": authority,
                        "postgres": {
                            "database": "hybench_sf10x",
                            "seven_parameter_baseline": copy.deepcopy(
                                v35.v34.EXPECTED_BASELINE
                            ),
                        },
                    }
                ),
                encoding="utf-8",
            )
            with (
                mock.patch.object(v35, "SOURCE_RESTORE_AUDIT", source),
                mock.patch.object(
                    v35,
                    "SOURCE_RESTORE_AUDIT_SHA256",
                    v35.v33.sha256(source),
                ),
                mock.patch.object(v35.v34, "_assert_affinity"),
                mock.patch.object(v35.v34, "_authority_paths", return_value=[]),
                mock.patch.object(
                    v35.v34,
                    "_sealed_hashes",
                    return_value=authority,
                ),
                mock.patch.object(
                    v35,
                    "verify_postgres_with_retry",
                    return_value={
                        "postgres": {
                            "database": "hybench_sf10x",
                            "seven_parameter_baseline": copy.deepcopy(
                                v35.v34.EXPECTED_BASELINE
                            ),
                        },
                        "attempt_count": 1,
                        "failure_count": 0,
                        "failures": [],
                    },
                ),
                mock.patch.object(v35.subprocess, "run") as run,
            ):
                audit = v35.reuse_restore_audit_before_r0(
                    campaign=campaign,
                    state_store=FakeStore(),
                    config=config,
                    restore_script=restore,
                )
            run.assert_not_called()
            self.assertFalse(audit["restore_reexecuted"])
            self.assertEqual(audit["physical_workload_calls_added"], 0)

    def test_v34_partial_is_technical_only(self) -> None:
        path = Path(
            "/home/anonymous/program/resource_adaptive/history/"
            "xp_v34_partial_r0_builder_technical_audit_20260803.json"
        )
        audit = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(audit["physical_workload_calls"], 0)
        self.assertEqual(audit["valid_replays"], 0)
        self.assertEqual(audit["technical_failures"], 1)
        self.assertFalse(audit["formal_state_inheritance_allowed"])
        self.assertTrue(audit["restore_audit_reusable"])


if __name__ == "__main__":
    unittest.main()
