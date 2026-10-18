from __future__ import annotations

import copy
import unittest
from pathlib import Path

from tools import tpcc_autonomous_target10_controller_v25 as v25
from tools import tpcc_autonomous_target10_controller_v31_cross_run_stability as v31


PROJECT_ROOT = Path("/home/anonymous/program/resource_adaptive")
STATE = (
    PROJECT_ROOT
    / "history/tpcc_final_algorithm_fresh0_v30_20260803/"
    "autonomous_controller/controller_state.json"
)


def observation(index: int, *, io: float, score: float, io_window_cv: float) -> dict:
    return {
        "point_key": "exact-point",
        "params": {"seq_page_cost": 1.0},
        "actual_resource": {"cpu": 2.04, "io": io, "memory": 2.55},
        "actual_score": score,
        "summary_path": f"/sealed/summary_{index}.json",
        "summary_sha256": f"sha-{index}",
        "cpu_within_run_stable": True,
        "memory_within_run_stable": True,
        "io_coverage_complete": True,
        "io_within_run_cv_audit_only": io_window_cv,
        "source_mode": "diagnostic_validation",
        "source_rank": 1,
    }


class CompleteRunRepeatabilityTests(unittest.TestCase):
    def test_periodic_within_run_io_cv_is_audit_only(self) -> None:
        rows = [
            observation(1, io=27.5, score=4.780, io_window_cv=0.25),
            observation(2, io=28.0, score=4.782, io_window_cv=0.30),
            observation(3, io=27.8, score=4.781, io_window_cv=0.20),
        ]
        result = v31._repeatability(rows)
        self.assertTrue(result["passed"])
        self.assertTrue(result["aggregate_io_repeatable"])
        self.assertTrue(result["aggregate_score_repeatable"])
        self.assertEqual(
            result["io_within_run_cv_role"],
            "audit_only_periodic_phase_variation",
        )

    def test_three_complete_runs_are_required(self) -> None:
        rows = [
            observation(1, io=27.5, score=4.780, io_window_cv=0.20),
            observation(2, io=27.6, score=4.781, io_window_cv=0.20),
        ]
        result = v31._repeatability(rows)
        self.assertFalse(result["enough_complete_run_repeats"])
        self.assertFalse(result["passed"])

    def test_unrepeatable_complete_run_io_fails_closed(self) -> None:
        rows = [
            observation(1, io=18.0, score=4.70, io_window_cv=0.05),
            observation(2, io=28.0, score=4.78, io_window_cv=0.05),
            observation(3, io=38.0, score=4.65, io_window_cv=0.05),
        ]
        result = v31._repeatability(rows)
        self.assertFalse(result["aggregate_io_repeatable"])
        self.assertFalse(result["passed"])

    def test_real_v30_pair_routes_to_third_repeat_zero_call(self) -> None:
        state = v25.load_json(STATE)
        diagnostic = next(
            item
            for item in state["audits"]
            if str(item.get("root") or "").endswith("autonomous_diagnostic_01")
        )
        validation = next(
            item
            for item in state["audits"]
            if str(item.get("root") or "").endswith(
                "autonomous_diagnostic_validation_02"
            )
        )
        selection = v25.load_json(Path(validation["selection_path"]))
        ledger = v25.load_json(Path(validation["ledger_path"]))
        upgraded = v31.apply_cross_run_stability_audit(
            validation,
            selection,
            ledger,
            [diagnostic],
        )
        self.assertEqual(upgraded["minimum_total_exact_observation_count"], 2)
        self.assertEqual(upgraded["maximum_total_exact_observation_count"], 2)
        self.assertEqual(upgraded["stable_trusted_count"], 0)
        self.assertTrue(upgraded["formal_admission_blocked_by_score_stability"])
        gate = {"passed": False}
        mode = v31.choose_next_mode_v31([copy.deepcopy(upgraded)], gate)
        self.assertEqual(mode, "diagnostic_validation")
        self.assertTrue(gate["complete_run_repeatability_reroute"])


if __name__ == "__main__":
    unittest.main()
