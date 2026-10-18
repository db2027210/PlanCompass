#!/usr/bin/env python3
"""End-to-end fake tests for the sealed JOB Algorithm C outer."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from contextlib import contextmanager

import job_algorithm_c_outer as outer
import job_session_local_runner
import prepare
from test_job_session_local_runner import FakeFactory


class AlgorithmCOuterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.sql_dir = self.root / "sql"
        self.sql_dir.mkdir()
        self.sql = self.sql_dir / "1a.sql"
        self.sql.write_text("SELECT 1\n", encoding="utf-8")
        contract = prepare.read_json(prepare.CONTRACT_PATH)
        self.contract = contract
        self.r0 = self.root / "r0.json"
        self.r0.write_text(json.dumps({
            "status": "complete",
            "all_three_r0_passed": True,
            "profiles": [
                {
                    "db_qps": row["db_qps"],
                    "resource_utilization": row["resource_utilization"],
                }
                for row in contract["r0_profiles"]
            ],
        }), encoding="utf-8")
        source_table = (
            prepare.PACKAGE_ROOT.parent.parent
            / contract["experience_table"]["path"]
        )
        self.experience_table = self.root / "experience_table.json"
        self.experience_table.write_bytes(source_table.read_bytes())
        self.output = self.root / "attempt"
        self.config = {
            "parameter_application_contract": copy.deepcopy(
                contract["parameter_application_contract"]
            ),
            "initial_params": copy.deepcopy(contract["initial_params"]),
            "param_ranges": copy.deepcopy(contract["parameter_ranges"]),
            "optimizer": {
                "active_params": list(contract["parameter_ranges"]),
            },
            "_algorithm_c_ratio_resource_only": copy.deepcopy(contract["algorithm_c"]),
            "formal_outer_protocol": {
                "r0_summary_path": str(self.r0),
                "r0_summary_sha256": outer.sha256_file(self.r0),
                "experience_table_path": str(self.experience_table),
                "experience_table_sha256": outer.sha256_file(
                    self.experience_table
                ),
                "output_root": str(self.output),
            },
            "_job_attempt8e_binding": {
                "r0_summary_sha256": outer.sha256_file(self.r0),
                "experience_table_sha256": outer.sha256_file(
                    self.experience_table
                ),
                "profile": copy.deepcopy(contract["profile"]),
            },
            "job_session_runner": {
                "endpoint": {
                    "host": "127.0.0.1",
                    "port": 64311,
                    "database": "imdb_job",
                    "user": "postgres",
                    "password_env": "JOB_OUTER_TEST_PASSWORD",
                },
                "sql_dir": str(self.sql_dir),
                "query_sequence": ["1a.sql"],
                "query_weights": [100],
                "query_sha256": {
                    "1a.sql": hashlib.sha256(self.sql.read_bytes()).hexdigest()
                },
                "workers": 2,
                "duration_seconds": 60,
                "max_queries_per_worker": 1,
                "resource_sample_interval_seconds": 1,
                "stability_window_seconds": 30,
                "required_stability_windows": 6,
            },
        }
        self.old_password = job_session_local_runner.os.environ.get(
            "JOB_OUTER_TEST_PASSWORD"
        )
        job_session_local_runner.os.environ["JOB_OUTER_TEST_PASSWORD"] = "CHANGE_ME"
        self.factories = []

    def tearDown(self) -> None:
        if self.old_password is None:
            job_session_local_runner.os.environ.pop("JOB_OUTER_TEST_PASSWORD", None)
        else:
            job_session_local_runner.os.environ[
                "JOB_OUTER_TEST_PASSWORD"
            ] = self.old_password
        self.temp.cleanup()

    def evaluator(self, accept: bool):
        def evaluate(config, candidate, point_dir):
            resources = point_dir / "measurement_resource_samples.jsonl"
            with resources.open("x", encoding="utf-8") as handle:
                for index in range(180):
                    handle.write(json.dumps({
                        "end_epoch": index + 1,
                        "cpu_utilization": 0.40,
                        "memory_utilization": 0.80,
                        "io_utilization": 0.30,
                    }) + "\n")
            candidate_config = copy.deepcopy(dict(config))
            candidate_config["initial_params"] = copy.deepcopy(candidate["params"])
            candidate_config["job_session_runner"]["output_dir"] = str(point_dir)
            candidate_config["job_session_runner"]["resource_samples_path"] = str(resources)
            factory = FakeFactory()
            self.factories.append(factory)
            result = job_session_local_runner.run(candidate_config, connect=factory)
            incumbent_qps = outer._r0_incumbent(config)["qps"]
            result["measurement"]["db_qps"] = (
                incumbent_qps + 0.1 if accept else incumbent_qps
            )
            result["measurement"]["qps"] = result["measurement"]["db_qps"]
            return result
        return evaluate

    def test_rejected_point_does_not_shrink_ranges(self) -> None:
        result = outer.run_outer(
            self.config,
            evaluator=self.evaluator(False),
            max_real_points=1,
        )
        self.assertEqual(result["accepted_count"], 0)
        self.assertEqual(result["ranges"], self.contract["parameter_ranges"])
        self.assertFalse(result["rounds"][0]["candidates"][0]["gate"]["accepted"])

    def test_accepted_point_shrinks_and_uses_session_local_runner(self) -> None:
        result = outer.run_outer(
            self.config,
            evaluator=self.evaluator(True),
            max_real_points=1,
        )
        self.assertEqual(result["accepted_count"], 1)
        changed = result["rounds"][0]["candidates"][0]["changed_parameter"]
        self.assertNotEqual(
            result["ranges"][changed],
            self.contract["parameter_ranges"][changed],
        )
        unchanged = set(self.contract["parameter_ranges"]) - {changed}
        for name in unchanged:
            self.assertEqual(
                result["ranges"][name],
                self.contract["parameter_ranges"][name],
            )
        local_statements = [
            sql
            for factory in self.factories
            for sql in factory.statements
            if sql.startswith("SET LOCAL ")
        ]
        self.assertEqual(
            len(local_statements),
            2 * len(job_session_local_runner.PARAMETERS),
        )
        self.assertTrue(
            result["rounds"][0]["candidates"][0]["gate"]["checks"][
                "global_defaults_unchanged"
            ]
        )

    def test_legacy_dataset_dependency_fails_before_any_point(self) -> None:
        self.config["formal_outer_protocol"]["dataset_path"] = "/legacy/72-points.json"
        with self.assertRaisesRegex(outer.OuterError, "legacy dataset/ranker"):
            outer.run_outer(self.config, evaluator=self.evaluator(True), max_real_points=1)
        self.assertEqual(self.factories, [])

    def test_experience_table_sha_drift_fails_closed(self) -> None:
        value = json.loads(self.experience_table.read_text(encoding="utf-8"))
        value["table"]["seq_page_cost"]["memory"] = 99
        self.experience_table.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(outer.OuterError, "bound artifact SHA drift"):
            outer.run_outer(self.config, evaluator=self.evaluator(True), max_real_points=1)
        self.assertEqual(self.factories, [])

    def test_sorted_json_key_order_does_not_change_active_parameter_order(self) -> None:
        serialized = json.dumps(self.config, sort_keys=True)
        sorted_config = json.loads(serialized)
        table = outer.validate_runtime(sorted_config)
        self.assertEqual(
            table["active_params"],
            self.config["optimizer"]["active_params"],
        )

    def test_inner_order_ignores_qps_and_freezes_low_confidence(self) -> None:
        table = json.loads(self.experience_table.read_text(encoding="utf-8"))
        incumbent = outer._r0_incumbent(self.config)
        before = outer.generate_candidates(
            incumbent, self.config["param_ranges"], table, []
        )
        for index, row in enumerate(table["table"].values()):
            row["qps"] = 1e9 if index % 2 else -1e9
        after = outer.generate_candidates(
            incumbent, self.config["param_ranges"], table, []
        )
        self.assertEqual(
            [row["candidate_id"] for row in before],
            [row["candidate_id"] for row in after],
        )
        self.assertTrue(all(
            row["resource_prior"]["prior_active"] for row in before[:8]
        ))
        self.assertTrue(all(
            not row["resource_prior"]["prior_active"] for row in before[8:]
        ))
        self.assertTrue(all(
            row["resource_prior"]["memory_direction_effect"] == 0.0
            and row["resource_prior"]["confidence"] == 0.0
            for row in before[8:]
        ))
        for name in self.config["param_ranges"]:
            self.assertEqual(
                {
                    row["direction"]
                    for row in before
                    if row["changed_parameter"] == name
                },
                {"up", "down"},
            )

    def test_production_evaluator_starts_fresh_sampler_around_actual_runner(self) -> None:
        events = []
        factory = FakeFactory()
        original_run = job_session_local_runner.run

        class FakeSampler:
            def __init__(self, profile, interval):
                self.profile = profile
                self.interval = interval

            def start(self, path):
                events.append("sampler_start")
                self.assert_fresh = not path.exists()
                with path.open("x", encoding="utf-8") as handle:
                    for index in range(180):
                        handle.write(json.dumps({
                            "end_epoch": index + 1,
                            "cpu_utilization": 0.4,
                            "memory_utilization": 0.8,
                            "io_utilization": 0.3,
                        }) + "\n")

            def stop(self):
                events.append("sampler_stop")

        def run_actual(candidate_config):
            events.append("session_runner")
            return original_run(candidate_config, connect=factory)

        @contextmanager
        def fake_fresh_container(fake_point_dir):
            events.append("fresh_restore")
            yield {"restore": {"test": True}, "container": {"test": True}}
            outer.atomic_json(
                fake_point_dir / "fresh_cleanup.json",
                {"cleanup_complete": True},
            )
            events.append("fresh_cleanup")

        experience_table = outer.validate_runtime(self.config)
        candidate = outer.generate_candidates(
            outer._r0_incumbent(self.config),
            self.config["param_ranges"],
            experience_table,
            [],
        )[0]
        point_dir = self.root / "production-evaluator-point"
        point_dir.mkdir()
        with mock.patch.object(
            outer.formal_cgroup_v1_sampler,
            "CgroupV1Sampler",
            FakeSampler,
        ), mock.patch.object(
            outer.job_session_local_runner,
            "run",
            side_effect=run_actual,
        ), mock.patch.object(
            outer.job_fresh_container,
            "fresh_container",
            side_effect=fake_fresh_container,
        ):
            result = outer.evaluate_real_candidate(self.config, candidate, point_dir)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(
            events,
            [
                "fresh_restore",
                "sampler_start",
                "session_runner",
                "sampler_stop",
                "fresh_cleanup",
            ],
        )
        self.assertTrue((point_dir / "candidate_live_config.json").is_file())
        self.assertEqual(
            len([sql for sql in factory.statements if sql.startswith("SET LOCAL ")]),
            2 * len(job_session_local_runner.PARAMETERS),
        )

    def test_rejected_basin_is_exhausted_in_rounds_of_at_most_five(self) -> None:
        incumbent = outer._r0_incumbent(self.config)

        def reject(_config, _candidate, _point_dir):
            return {
                "status": "complete",
                "measurement": {
                    "correctness_passed": True,
                    "resource_stability": {"passed": True},
                    "db_qps": incumbent["qps"],
                    "resource_mean_utilization": {
                        "cpu": 0.4,
                        "memory": 0.8,
                        "io": 0.3,
                    },
                },
                "cleanup": {"cleanup_complete": True},
                "readback": {"global_defaults_unchanged": True},
            }

        result = outer.run_outer(self.config, evaluator=reject)
        self.assertEqual(result["status"], "basin_exhausted")
        self.assertEqual(result["accepted_count"], 0)
        self.assertEqual(result["real_point_count"], 14)
        self.assertEqual(
            [len(round_row["candidates"]) for round_row in result["rounds"]],
            [5, 5, 4],
        )
        self.assertEqual(result["ranges"], self.contract["parameter_ranges"])


if __name__ == "__main__":
    unittest.main()
