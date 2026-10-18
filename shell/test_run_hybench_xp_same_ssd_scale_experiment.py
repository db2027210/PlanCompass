#!/usr/bin/env python3
"""Unit tests for the HyBench XP same-SSD scale controller."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import run_hybench_xp_same_ssd_scale_experiment as scale_module


def args(**overrides: object) -> argparse.Namespace:
    values = {
        "phase": "plan",
        "tag": "unit_test",
        "counts": (1, 4, 8, 12, 16),
        "repeats": 3,
        "warmup_minutes": 1,
        "measurement_minutes": 10,
        "cooldown_seconds": 0,
        "seed_pgdata": Path("/tmp/seed"),
        "max_host_load": 32.0,
        "allow_busy_host": False,
        "resume": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class ScaleExperimentTests(unittest.TestCase):
    def test_resume_accepts_json_normalized_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            experiment = scale_module.ScaleExperiment(args(resume=True))
            experiment.root = Path(temporary)
            (experiment.root / "manifest.json").write_text(
                json.dumps(experiment.manifest()),
                encoding="utf-8",
            )

            experiment.ensure_root()

    def test_reset_database_runs_drop_in_separate_psql_call(self) -> None:
        experiment = scale_module.ScaleExperiment(args())
        with mock.patch.object(experiment, "psql") as psql:
            experiment.reset_database("i01")

        calls = psql.call_args_list
        self.assertEqual(len(calls), 4)
        self.assertIn("pg_terminate_backend", calls[0].args[2])
        self.assertNotIn("DROP DATABASE", calls[0].args[2])
        self.assertEqual(
            calls[1].args,
            ("i01", "postgres", "DROP DATABASE IF EXISTS hybench_sf10x WITH (FORCE);"),
        )
        self.assertIn("CREATE DATABASE", calls[2].args[2])
        self.assertIn("CHECKPOINT", calls[3].args[2])

    def test_count_orders_cover_every_count(self) -> None:
        experiment = scale_module.ScaleExperiment(args())
        expected = {1, 4, 8, 12, 16}
        for order in experiment.count_orders():
            self.assertEqual(set(order), expected)
            self.assertEqual(len(order), len(expected))

    def test_cpu_allocations_are_disjoint_and_numa_local(self) -> None:
        used = set()
        for item in range(1, 17):
            allocation = scale_module.ScaleExperiment.cpu_allocation(item)
            cpus = set(allocation["db_cpus"]) | set(allocation["client_cpus"])
            self.assertEqual(len(allocation["db_cpus"]), 10)
            self.assertEqual(len(allocation["client_cpus"]), 4)
            self.assertFalse(used.intersection(cpus))
            used.update(cpus)
            if allocation["node"] == 0:
                self.assertTrue(all(cpu < 128 for cpu in cpus))
            else:
                self.assertTrue(all(128 <= cpu < 256 for cpu in cpus))

    def test_parse_counts(self) -> None:
        self.assertEqual(scale_module.parse_counts("1,4,8,12,16"), (1, 4, 8, 12, 16))
        with self.assertRaises(argparse.ArgumentTypeError):
            scale_module.parse_counts("4,8,12,16")

    def test_validate_log(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "console.log").write_text(
                "XP Workload is done\nXP-QPS is 7.25\nXP-TPS is 28.50\n",
                encoding="utf-8",
            )
            result = scale_module.ScaleExperiment(args()).validate_log(directory)
            self.assertEqual(result, {"qps": 7.25, "tps": 28.5})

    def test_validate_log_rejects_silent_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "console.log").write_text(
                "XP Workload is done\nNullPointerException\nXP-QPS is 0\nXP-TPS is 0\n",
                encoding="utf-8",
            )
            with self.assertRaises(scale_module.ExperimentError):
                scale_module.ScaleExperiment(args()).validate_log(directory)

    def test_parse_iostat(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "iostat.log"
            path.write_text(
                "nvme1n1 1 100 0 0 0.1 100 2 200 0 0 0.2 100 0 0 0 0 0 0 1 10\n"
                "nvme1n1 2 300 0 0 0.3 150 4 400 0 0 0.4 100 0 0 0 0 0 0 2 30\n",
                encoding="utf-8",
            )
            result = scale_module.ScaleExperiment.parse_iostat(path)
            self.assertEqual(result["read_kib_s_mean"], 200.0)
            self.assertEqual(result["write_kib_s_mean"], 300.0)
            self.assertEqual(result["util_percent_mean"], 20.0)
            self.assertEqual(result["util_percent_max"], 30.0)

    def test_summary_upper_limit_is_contiguous(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            experiment = scale_module.ScaleExperiment(args())
            experiment.root = Path(temporary)
            profiles = {
                1: (10.0, 20.0, 10.0),
                4: (9.8, 19.5, 25.0),
                8: (9.5, 19.0, 50.0),
                12: (8.5, 17.0, 70.0),
                16: (10.0, 20.0, 20.0),
            }
            for count, (qps, tps, io_util) in profiles.items():
                for repeat in (1, 2, 3):
                    directory = experiment.root / "results" / f"N{count:02d}_rep{repeat:02d}"
                    directory.mkdir(parents=True)
                    metrics = {
                        f"i{item:02d}": {"qps": qps, "tps": tps}
                        for item in range(1, count + 1)
                    }
                    (directory / "completed.json").write_text(
                        json.dumps(
                            {
                                "count": count,
                                "repeat": repeat,
                                "metrics": metrics,
                                "io": {
                                    "util_percent_mean": io_util,
                                    "util_percent_max": io_util,
                                },
                            }
                        ),
                        encoding="utf-8",
                    )
            experiment.summarize()
            summary = json.loads(
                (experiment.root / "summary.json").read_text(encoding="utf-8")
            )
            self.assertEqual(summary["upper_limit"], 8)
            self.assertEqual(summary["first_failure"]["count"], 12)


if __name__ == "__main__":
    unittest.main()
