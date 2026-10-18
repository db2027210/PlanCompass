#!/usr/bin/env python3
"""Regression tests for the isolated JOB target-only v5 stability runtime."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    ROOT
    / "launch_packages/pg5_common_outer_bgallowed_20260722_v1/job_node02"
    / "attempt8_workspace/formal_measurement.py"
)
CONTRACT_PATH = (
    ROOT
    / "config/formal/pg5_common_outer_bgallowed_20260722_v1"
    / "stability_gate_contract_v5.json"
)
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location("job_v5_formal_measurement", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load formal measurement module: {MODULE_PATH}")
FORMAL = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = FORMAL
SPEC.loader.exec_module(FORMAL)


def measurement_protocol() -> dict:
    return {
        "schema_version": 1,
        "immutable": True,
        "config_id": "measurement_protocol_20260723_v17_job_target_memory_v5",
        "timing": {
            "sample_interval_seconds": 1,
            "warmup_seconds": 60,
            "minimum_measurement_seconds": 180,
            "initial_probe_seconds": 180,
            "decision_step_seconds": 30,
            "maximum_measurement_seconds": 600,
            "stability_window_seconds": 30,
            "required_consecutive_stability_windows": 6,
            "sample_coverage_min": 0.9,
            "continue_after_probe_when_unstable": True,
        },
        "stability": {
            "calculation": (
                "coefficient_of_variation_across_six_target_resource_window_means"
            ),
            "target_resource": "memory",
            "target_resource_field": "memory_utilization",
            "target_cv_max": 0.1,
            "diagnostic_cv_max": 0.1,
            "all_cpu_memory_io_resources_must_be_stable": False,
            "throughput_fields_required_for_stability": False,
            "throughput_role": "outer_acceptance_only",
            "candidate_profile_band_gate_enabled": False,
            "normal_throughput_calculation": "outer_acceptance_only",
            "normal_throughput_cv_max": 0.1,
            "xp_xap_qps_cv_max": 0.1,
            "xp_xtp_tps_cv_max": 0.1,
            "low_utilization_ratio_threshold": 0.1,
            "low_utilization_absolute_range_max": 0.05,
        },
    }


def protocol() -> object:
    payload = measurement_protocol()
    timing = payload["timing"]
    stability = payload["stability"]
    return FORMAL.FrozenMeasurementProtocol(
        sample_interval_seconds=timing["sample_interval_seconds"],
        warmup_seconds=timing["warmup_seconds"],
        minimum_measurement_seconds=timing["minimum_measurement_seconds"],
        initial_probe_seconds=timing["initial_probe_seconds"],
        decision_step_seconds=timing["decision_step_seconds"],
        maximum_measurement_seconds=timing["maximum_measurement_seconds"],
        stability_window_seconds=timing["stability_window_seconds"],
        required_consecutive_stability_windows=timing[
            "required_consecutive_stability_windows"
        ],
        sample_coverage_min=timing["sample_coverage_min"],
        cpu_cv_max=stability["diagnostic_cv_max"],
        memory_cv_max=stability["target_cv_max"],
        io_cv_max=stability["diagnostic_cv_max"],
        normal_throughput_cv_max=stability["normal_throughput_cv_max"],
        xp_xap_qps_cv_max=stability["xp_xap_qps_cv_max"],
        xp_xtp_tps_cv_max=stability["xp_xtp_tps_cv_max"],
        low_utilization_ratio_threshold=stability["low_utilization_ratio_threshold"],
        low_utilization_absolute_range_max=stability[
            "low_utilization_absolute_range_max"
        ],
        stability_calculation=stability["calculation"],
        normal_throughput_calculation=stability[
            "normal_throughput_calculation"
        ],
        target_resource_field=stability["target_resource_field"],
    )


def samples(window_memory: list[float]) -> list[dict]:
    rows = []
    for second in range(len(window_memory) * 30):
        window = second // 30
        rows.append(
            {
                "epoch_seconds": 1000.0 + second + 0.1,
                "cpu_utilization": 0.2 if window % 2 == 0 else 0.9,
                "memory_utilization": window_memory[window],
                "io_utilization": 0.9 if window % 2 == 0 else 0.2,
                "qps": 1.0 if window % 2 == 0 else 9.0,
            }
        )
    return rows


class TargetOnlyV5Test(unittest.TestCase):
    def test_loader_accepts_exact_v5_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "measurement_protocol.json").write_text(
                json.dumps(measurement_protocol()), encoding="utf-8"
            )
            (root / "stability_gate_contract.json").write_bytes(
                CONTRACT_PATH.read_bytes()
            )
            loaded = FORMAL.FrozenMeasurementProtocol.from_frozen_bundle(root)
            self.assertEqual(loaded.target_resource_field, "memory_utilization")
            self.assertEqual(loaded.initial_probe_seconds, 180)
            self.assertEqual(loaded.decision_step_seconds, 30)

    def test_non_targets_and_qps_cannot_block_stable_memory_at_180(self) -> None:
        assessment = FORMAL.assess_measurement_samples(
            samples([0.75] * 6),
            measurement_start_epoch_seconds=1000.0,
            now_epoch_seconds=1180.0,
            xp=False,
            protocol=protocol(),
        )
        fields = {item.field: item for item in assessment.cross_window_blocks[-1].fields}
        self.assertFalse(fields["cpu_utilization"].stable)
        self.assertTrue(fields["memory_utilization"].stable)
        self.assertFalse(fields["io_utilization"].stable)
        self.assertTrue(assessment.stable)
        self.assertEqual(assessment.earliest_stable_stop_seconds, 180)

    def test_unstable_memory_fails_even_when_non_targets_are_irrelevant(self) -> None:
        assessment = FORMAL.assess_measurement_samples(
            samples([0.40, 0.80, 0.40, 0.80, 0.40, 0.80]),
            measurement_start_epoch_seconds=1000.0,
            now_epoch_seconds=1180.0,
            xp=False,
            protocol=protocol(),
        )
        self.assertFalse(assessment.stable)
        self.assertIsNone(assessment.earliest_stable_stop_seconds)

    def test_latest_six_windows_can_pass_at_next_30_second_checkpoint(self) -> None:
        assessment = FORMAL.assess_measurement_samples(
            samples([0.30] + [0.75] * 6),
            measurement_start_epoch_seconds=1000.0,
            now_epoch_seconds=1210.0,
            xp=False,
            protocol=protocol(),
        )
        self.assertTrue(assessment.stable)
        self.assertEqual(assessment.earliest_stable_stop_seconds, 210)
        self.assertEqual(
            assessment.cross_window_blocks[-1].start_window_index,
            1,
        )


if __name__ == "__main__":
    unittest.main()
