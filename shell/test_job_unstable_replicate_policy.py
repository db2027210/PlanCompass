#!/usr/bin/env python3
"""Unit contracts for JOB same-point unstable replicate aggregation."""

from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
import unittest


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "launch_packages/pg5_common_outer_bgallowed_20260722_v1/job_node02/"
    "formal_job_outer_replay.py"
)
SPEC = importlib.util.spec_from_file_location("job_replicate_policy_tested", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class JobUnstableReplicatePolicyTest(unittest.TestCase):
    def test_first_stable_is_direct_but_first_unstable_forces_three(self) -> None:
        self.assertEqual(MODULE.required_valid_replicates_after_first(True), 1)
        self.assertEqual(MODULE.required_valid_replicates_after_first(False), 3)

    def test_composite_is_point_mean_of_exactly_three(self) -> None:
        base = {
            "semantics": "formal",
            "raw_range_validation": "finite",
            "io_formula": "linear",
            "caps": {"io_total_cap_bps": 10.0},
            "sample_count": 10,
            "aggregate": {
                "cpu_utilization": 0.3,
                "memory_utilization": 0.8,
                "io_utilization": 0.4,
                "read_bps": 3.0,
                "write_bps": 1.0,
                "total_bps": 4.0,
                "memory_working_set_bytes": 8.0,
            },
        }
        resources = []
        for delta in (-0.1, 0.0, 0.1):
            item = copy.deepcopy(base)
            item["aggregate"]["cpu_utilization"] += delta
            item["aggregate"]["memory_utilization"] += delta
            item["aggregate"]["io_utilization"] += delta
            resources.append(item)
        composite = MODULE.composite_formal_resource(resources)
        self.assertAlmostEqual(composite["aggregate"]["cpu_utilization"], 0.3)
        self.assertAlmostEqual(composite["aggregate"]["memory_utilization"], 0.8)
        self.assertAlmostEqual(composite["aggregate"]["io_utilization"], 0.4)
        self.assertEqual(composite["sample_count"], 30)
        with self.assertRaisesRegex(RuntimeError, "exactly three"):
            MODULE.composite_formal_resource(resources[:2])

    def test_unstable_is_valid_but_technical_failure_is_not(self) -> None:
        result = {
            "status": "complete",
            "oom_killed": False,
            "database_restart_count": 0,
            "measurement": {
                "correctness_passed": True,
                "db_qps": 2.0,
                "resource_stability": {"passed": False, "stable": False},
            },
        }
        MODULE.validate_technical_replicate(result)
        failed = copy.deepcopy(result)
        failed["measurement"]["correctness_passed"] = False
        with self.assertRaisesRegex(RuntimeError, "correctness"):
            MODULE.validate_technical_replicate(failed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
