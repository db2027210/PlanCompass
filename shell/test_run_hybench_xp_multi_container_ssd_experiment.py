#!/usr/bin/env python3
"""Unit tests for the multi-container SSD experiment controller."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import run_hybench_xp_multi_container_ssd_experiment as experiment_module


def args(**overrides: object) -> argparse.Namespace:
    values = {
        "phase": "summarize",
        "tag": "unit_test",
        "repeats": 3,
        "warmup_minutes": 1,
        "measurement_minutes": 10,
        "cap_profile": "low",
        "resume": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class ExperimentTests(unittest.TestCase):
    def test_case_orders_cover_all_cases(self) -> None:
        expected = set(experiment_module.CASE_LANES)
        for order in experiment_module.CASE_ORDERS:
            self.assertEqual(set(order), expected)
            self.assertEqual(len(order), len(expected))

    def test_parse_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "hybench.log").write_text(
                "Current 1/2 time AP QPS is 4.00\n"
                "Current 1/2 time TP TPS is 20.00\n"
                "Current 2/2 time AP QPS is 6.00\n"
                "Current 2/2 time TP TPS is 24.00\n",
                encoding="utf-8",
            )
            result = experiment_module.Experiment(args()).parse_metrics(root)
            self.assertEqual(result["ap_qps_mean"], 5.0)
            self.assertEqual(result["tp_tps_mean"], 22.0)
            self.assertEqual(result["ap_qps_windows"], 2)

    def test_validate_workload_log_rejects_silent_java_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "console.log").write_text(
                "Begin XP Workload\n"
                "Current 1/1 time AP QPS is 0.00\n"
                "Current 1/1 time TP TPS is 0.00\n"
                "java.lang.NullPointerException\n",
                encoding="utf-8",
            )
            with self.assertRaises(experiment_module.ExperimentError):
                experiment_module.Experiment(args()).validate_workload_log(root)

    def test_validate_workload_log_accepts_progress(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "hybench.log").write_text(
                "Begin XP Workload\n"
                "Current 1/1 time AP QPS is 4.00\n"
                "Current 1/1 time TP TPS is 20.00\n",
                encoding="utf-8",
            )
            experiment_module.Experiment(args()).validate_workload_log(root)

    def test_validate_props_rejects_query_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "bad.props"
            path.write_text(
                "xtpclient=16\nxapclient=6\n"
                "at1_percent=40\nat2_percent=30\nat3_percent=15\n"
                "at4_percent=5\nat5_percent=7\nat6_percent=3\n"
                "xpRunMins=10\nap_query_ids=1,2\n"
                "url=jdbc:postgresql://127.0.0.1:59631/hybench_sf10x\n"
                "url_tp=jdbc:postgresql://127.0.0.1:59631/hybench_sf10x\n"
                "url_ap=jdbc:postgresql://127.0.0.1:59631/hybench_sf10x\n",
                encoding="utf-8",
            )
            with self.assertRaises(experiment_module.ExperimentError):
                experiment_module.Experiment(args()).validate_props(path, 59631, 10)

    def test_manifest_is_stable(self) -> None:
        value = experiment_module.Experiment(args()).manifest()
        self.assertEqual(value["cap_profile"], "low")
        self.assertEqual(value["repeats"], 3)
        self.assertEqual(value["ports"]["a"], 59631)
        self.assertEqual(value["device_numbers"]["b_split"], "259:6")
        json.dumps(value, sort_keys=True)

    def test_io_limit_profiles(self) -> None:
        self.assertEqual(
            experiment_module.Experiment(args(cap_profile="low")).expected_io_limits(),
            (100000, 837500),
        )
        self.assertEqual(
            experiment_module.Experiment(args(cap_profile="64m")).expected_io_limits(),
            (67108864, 67108864),
        )
        self.assertEqual(
            experiment_module.Experiment(args(cap_profile="unlimited")).expected_io_limits(),
            (0, 0),
        )


if __name__ == "__main__":
    unittest.main()
