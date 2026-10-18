#!/usr/bin/env python3
"""Focused tests for immutable Algorithm C calibration reuse."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest


import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from algorithm_c_calibration_release import (  # noqa: E402
    calibration_compatibility_payload,
    calibration_compatibility_sha256,
    canonical_sha256,
    find_compatible_calibration_release,
    materialize_calibration_release,
    publish_calibration_release,
    verify_release_manifest,
)


def config(run_root: str, *, xapclient: int = 6) -> dict:
    return {
        "algorithm_c": {
            "scene": "xp_io",
            "benchmark": "hybench_xp",
            "active_params": ["seq_page_cost", "random_page_cost"],
            "initial_params": {"seq_page_cost": 1.0, "random_page_cost": 4.0},
            "expected_plan_query_count": 50,
            "resource_estimation_contract": "algorithm_c_executor_physics_plan_ratio_v2",
            "resource_estimation_contract_version": 2,
            "workload_contract": {
                "contract_version": 1,
                "benchmark": "hybench_xp",
                "target_resource": "io",
                "physical_replay": "native_HyBench_XP_default_full_workload",
                "plan_evidence": {
                    "path": f"{run_root}/native_xp_full_plan_representatives.sql",
                    "sha256": "plan-sha",
                    "query_count": 50,
                },
                "database": {
                    "dbname": "hybench_sf10x",
                    "template_db": "hybench_sf10x_base",
                    "workers": [
                        {
                            "name": "w1",
                            "container": "container-a",
                            "host": "127.0.0.1",
                            "port": 1111,
                            "data_dir": f"{run_root}/w1",
                            "cpuset_cpus": "1-4",
                        },
                        {
                            "name": "w2",
                            "container": "container-b",
                            "host": "127.0.0.1",
                            "port": 2222,
                            "data_dir": f"{run_root}/w2",
                            "cpuset_cpus": "5-8",
                        },
                    ],
                },
                "native_xp": {
                    "xtpclient": 18,
                    "xapclient": xapclient,
                    "at_percentages": {"at1_percent": 40},
                    "statement_file": f"{run_root}/stmt.toml",
                    "statement_file_sha256": "stmt-sha",
                    "native_jar_file": f"{run_root}/hybench.jar",
                    "native_jar_sha256": "jar-sha",
                },
                "resource_capacity": {
                    "cpu_cores": 30,
                    "memory_gib": 24,
                    "io_mib_s": 22.2,
                },
            },
        },
        "metrics": {"plan_estimator": {"physical_model": {"contract": "v2"}}},
        "plan_capture": {"modes": ["generic"]},
        "param_ranges": {"seq_page_cost": {"min_value": 0.1, "max_value": 10}},
        "binary_plan_tuning": {
            "validation": {
                "hybench_xp": {
                    "xapclient": xapclient,
                    "xtpclient": 18,
                    "statement_file": f"{run_root}/stmt.toml",
                    "statement_file_sha256": "stmt-sha",
                },
                "docker_runtime": {"memory_mode": "working_set"},
                "executor_telemetry": {"enabled": True},
                "shared_disk_restore_barrier": {"enabled": True},
            }
        },
    }


class CalibrationReleaseTest(unittest.TestCase):
    def test_run_local_paths_and_worker_endpoints_do_not_change_signature(self) -> None:
        first = config("/run/one")
        second = config("/run/two")
        second["algorithm_c"]["workload_contract"]["database"]["workers"][0]["port"] = 9999
        self.assertEqual(
            calibration_compatibility_sha256(first),
            calibration_compatibility_sha256(second),
        )

    def test_workload_concurrency_change_invalidates_signature(self) -> None:
        self.assertNotEqual(
            calibration_compatibility_sha256(config("/run", xapclient=6)),
            calibration_compatibility_sha256(config("/run", xapclient=7)),
        )

    def test_hybench_contract_is_present_in_compatibility_payload(self) -> None:
        payload = calibration_compatibility_payload(config("/run"))
        self.assertIsInstance(payload["validation"]["hybench_xp"], dict)
        self.assertTrue(payload["validation"]["hybench_xp"])

    def test_target_support_contract_change_invalidates_signature(self) -> None:
        disabled = config("/run")
        enabled = config("/run")
        enabled["algorithm_c"]["target_support_abstention_enabled"] = True
        self.assertNotEqual(
            calibration_compatibility_sha256(disabled),
            calibration_compatibility_sha256(enabled),
        )

    def test_publish_find_verify_and_materialize(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            release_root = root / "releases"
            source.mkdir()
            cfg = config(str(source))
            artifact = {
                "schema_version": 5,
                "formal_admission": {
                    "ready": True,
                    "scored_resource_admission_contract": (
                        "hard_structural_prediction_quality_diagnostic_v3"
                    ),
                    "minimum_train_count": 7,
                    "minimum_holdout_count": 3,
                },
                "fit": {"train_count": 8, "holdout_count": 4},
                "table": {},
            }
            artifact["artifact_sha256"] = canonical_sha256(artifact)
            artifact_path = source / "experience_table.json"
            artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
            (source / "optimizer_config.json").write_text(
                json.dumps(cfg), encoding="utf-8"
            )
            (source / "workload_contract.json").write_text(
                json.dumps(cfg["algorithm_c"]["workload_contract"]),
                encoding="utf-8",
            )
            (source / "calibration_replays.jsonl").write_text(
                '{"event":"candidate_validation"}\n', encoding="utf-8"
            )
            (source / "calibration_plan_predictions.json").write_text(
                json.dumps({
                    "baseline_plan_fingerprint": "workload-exact-physical-sha256:test",
                    "baseline_plan_coverage": {"complete": True},
                }),
                encoding="utf-8",
            )

            published = publish_calibration_release(
                cfg=cfg,
                source_dir=source,
                artifact_path=artifact_path,
                release_root=release_root,
            )
            verified = verify_release_manifest(
                Path(published["manifest_path"]), cfg=cfg
            )
            found = find_compatible_calibration_release(
                cfg, release_root=release_root
            )
            destination = root / "new-run" / "experience_table.json"
            materialized = materialize_calibration_release(
                found or {}, destination=destination
            )
            self.assertEqual(
                verified["manifest_sha256"], published["manifest_sha256"]
            )
            self.assertIsNotNone(found)
            self.assertEqual(
                materialized["artifact_internal_sha256"],
                artifact["artifact_sha256"],
            )
            self.assertEqual(
                published["required_release_sample_counts"],
                {"train_count": 7, "holdout_count": 3},
            )
            self.assertEqual(json.loads(destination.read_text()), artifact)

    def test_corrupt_release_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            cfg = config(str(source))
            artifact = {
                "formal_admission": {"ready": True},
                "fit": {"train_count": 10, "holdout_count": 5},
            }
            artifact["artifact_sha256"] = canonical_sha256(artifact)
            artifact_path = source / "experience_table.json"
            artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
            for name, value in {
                "optimizer_config.json": cfg,
                "workload_contract.json": cfg["algorithm_c"]["workload_contract"],
                "calibration_plan_predictions.json": {
                    "baseline_plan_fingerprint": "fingerprint",
                    "baseline_plan_coverage": {"complete": True},
                },
            }.items():
                (source / name).write_text(json.dumps(value), encoding="utf-8")
            (source / "calibration_replays.jsonl").write_text("{}\n", encoding="utf-8")
            published = publish_calibration_release(
                cfg=cfg,
                source_dir=source,
                artifact_path=artifact_path,
                release_root=root / "releases",
            )
            released_artifact = Path(published["manifest_path"]).parent / "experience_table.json"
            released_artifact.write_text("{}\n", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "evidence SHA mismatch"):
                verify_release_manifest(Path(published["manifest_path"]), cfg=cfg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
