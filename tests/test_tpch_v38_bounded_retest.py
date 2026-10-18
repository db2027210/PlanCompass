from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from tools import build_tpch_autonomous_successor_top5_v9_bounded_retest as builder
from tools import tpch_fresh0_target10_v38_bounded_retest as controller


def candidate(index: int) -> dict:
    label = f"{index:064x}"
    params = {
        "cpu_index_tuple_cost": 0.001 + index * 0.0001,
        "cpu_operator_cost": 0.0025,
        "cpu_tuple_cost": 0.01,
        "parallel_setup_cost": 1000 + index,
        "parallel_tuple_cost": 0.1,
        "random_page_cost": 4,
        "seq_page_cost": 1,
    }
    return {
        "rank": index,
        "selection_role": "probe",
        "candidate_params": params,
        "candidate_score": 4.0 + index * 0.01,
        "predicted_resource": {"cpu": 0.4, "io": 16.0, "memory": 1.3},
        "utilization": {"cpu": 0.4, "io": 0.4, "memory": 0.86},
        "feature_ratio": {"cpu": 1.0, "io": 1.0, "memory": 1.0},
        "dScore/dU": {"cpu": 1.0, "io": 1.0, "memory": 1.0},
        "T": 1.0,
        "confidence": {"measurement_count": 0},
        "g_j": {"cpu": 1.0},
        "eta": 0.1,
        "delta_param": {"cpu_index_tuple_cost": 0.0001},
        "boundary_corner": False,
        "boundary_plateau": False,
        "actual_parameter_noncorner_audit": {
            "reason": "actual_parameter_space_noncorner"
        },
        "three_level_fingerprint": {
            "id": f"three-level-sha256:{label}",
            "plan_level": f"ratio-plan-sha256:{label}",
            "feature_ratio_level": {"cpu": index, "io": 2, "memory": 3},
        },
        "final_estimation_metadata": {
            "query_features": [
                {
                    "queryid": "1",
                    "plan_fingerprint": f"exact-structure-sha256:{label}",
                }
            ]
        },
    }


class TpchV38BoundedRetestTests(unittest.TestCase):
    def test_global_exclusions_include_v38_cycle_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            loop = root / "target10_loop_v38"
            cycle = loop / "cycle_004"
            cycle.mkdir(parents=True)
            historical = cycle / "probe_top5.a.json"
            historical.write_text(
                json.dumps({"top5": [candidate(index) for index in range(1, 6)]}),
                encoding="utf-8",
            )

            def old_paths(_root: Path):
                return iter(())

            paths = list(
                controller.selection_paths_with_loop(old_paths, root, loop)
            )
        self.assertEqual(paths, [historical.resolve()])

    def test_v40_global_exclusions_ignore_unexecuted_v38_v39_top5(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            v38_cycle = root / "target10_loop_v38/cycle_004"
            v39_cycle = root / "target10_loop_v39/cycle_004"
            v40_cycle = root / "target10_loop_v40/cycle_004"
            v38_cycle.mkdir(parents=True)
            v39_cycle.mkdir(parents=True)
            v40_cycle.mkdir(parents=True)
            unexecuted = v38_cycle / "probe_top5.a.json"
            unexecuted_v39 = v39_cycle / "probe_top5.a.json"
            executed = v40_cycle / "probe_top5.a.json"
            payload = {"top5": [candidate(index) for index in range(1, 6)]}
            unexecuted.write_text(json.dumps(payload), encoding="utf-8")
            unexecuted_v39.write_text(json.dumps(payload), encoding="utf-8")
            executed.write_text(json.dumps(payload), encoding="utf-8")

            paths = list(
                controller.selection_paths_with_loop(
                    lambda _root: iter(()),
                    root,
                    root / "target10_loop_v40",
                )
            )
        self.assertEqual(paths, [executed.resolve()])
        self.assertNotIn(unexecuted.resolve(), paths)
        self.assertNotIn(unexecuted_v39.resolve(), paths)

    def test_failed_v38_zero_workload_audit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "v38"
            loop = root / "target10_loop_v38"
            cycle = loop / "cycle_004"
            cycle.mkdir(parents=True)
            counter = root / "formal_search_accepted_counter.txt"
            counter.write_text("0\n", encoding="utf-8")
            selection = cycle / "probe_top5.a.json"
            selection.write_text(
                json.dumps({"top5": [candidate(index) for index in range(1, 6)]}),
                encoding="utf-8",
            )
            audit = controller.audit_failed_zero_workload(
                root, loop, counter, "v38"
            )
        self.assertEqual(audit["physical_workload_calls"], 0)
        self.assertTrue(audit["selection_reuse_allowed"])
        self.assertTrue(audit["excluded_from_v40_global_novelty_history"])

    def test_failed_v38_audit_rejects_any_physical_call(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "v38"
            loop = root / "target10_loop_v38"
            cycle = loop / "cycle_004"
            cycle.mkdir(parents=True)
            counter = root / "formal_search_accepted_counter.txt"
            counter.write_text("0\n", encoding="utf-8")
            (cycle / "probe_top5.a.json").write_text(
                json.dumps({"top5": [candidate(index) for index in range(1, 6)]}),
                encoding="utf-8",
            )
            replay = cycle / "diagnostic_probe/diagnostic_state.json"
            replay.parent.mkdir()
            replay.write_text(
                json.dumps({"physical_call_count": 1}), encoding="utf-8"
            )
            with self.assertRaisesRegex(RuntimeError, "physical workload call"):
                controller.audit_failed_zero_workload(
                    root, loop, counter, "v38"
                )

    def test_unknown_ucb_bonus_is_bounded(self) -> None:
        prior = {
            "evidence_count": 2,
            "scalar_median": {"cpu": 1.0, "io": 1.0, "memory": 1.0},
            "scalar_lower": {"cpu": 0.1, "io": 0.1, "memory": 0.1},
            "scalar_upper": {"cpu": 10.0, "io": 10.0, "memory": 10.0},
        }
        calibrated = builder.hierarchical_calibrate_probe(candidate(1), prior)
        bonus = (
            calibrated["probe_acquisition_score"]
            - calibrated["candidate_score"]
        )
        self.assertGreaterEqual(bonus, 0.0)
        self.assertLessEqual(bonus, builder.MAX_UNKNOWN_UCB_BONUS + 1e-12)

    def test_low_expected_probe_cannot_pass_with_ucb(self) -> None:
        value = candidate(1)
        value["candidate_score"] = 4.0
        value["probe_acquisition_score"] = 4.9
        selected, audit = builder.score_ladder_probe_take([value], 1, 4.5)
        self.assertEqual(selected, [])
        self.assertEqual(audit["feasible_count"], 0)

    def test_clean_probe5_becomes_non_novel_formal_neutral_retest(self) -> None:
        selection = {
            "diagnostic_only": True,
            "accepted_count_start": 0,
            "validated_plan_regimes": {},
            "top5": [candidate(index) for index in range(1, 6)],
        }
        successful = []
        for item in selection["top5"]:
            rank = item["rank"]
            successful.append(
                {
                    "rank": rank,
                    "selection_role": "probe",
                    "three_level_fingerprint": item["three_level_fingerprint"],
                    "candidate_params": item["candidate_params"],
                    "technical_failure": False,
                    "valid_replay": True,
                    "actual_score": 3.9 + rank * 0.01,
                    "actual_resource": {
                        "cpu": 0.35 + rank * 0.01,
                        "io": 16.0,
                        "memory": 1.2,
                    },
                }
            )
        summary = {
            "diagnostic_only": True,
            "accepted_count": 0,
            "physical_call_count": 5,
            "valid_replay_count": 5,
            "technical_failure_count": 0,
            "baseline_restored": True,
            "accepted_update_applied": False,
            "incumbent_update_applied": False,
            "range_update_applied": False,
            "gradient_update_applied": False,
            "successful": successful,
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            selection_path = root / "selection.json"
            summary_path = root / "summary.json"
            expansion_path = root / "expansion.json"
            output_path = root / "output.json"
            selection_path.write_text(json.dumps(selection), encoding="utf-8")
            summary_path.write_text(json.dumps(summary), encoding="utf-8")
            expansion_path.write_text(
                json.dumps({"ranked_candidates": []}), encoding="utf-8"
            )
            output = builder.build(
                selection_path,
                summary_path,
                expansion_path,
                output_path,
            )
        self.assertEqual(output["novel_unique_fingerprint_count"], 0)
        self.assertEqual(
            [item["selection_role"] for item in output["top5"]],
            ["trusted", "trusted", "trusted", "probe", "probe"],
        )
        for item in output["top5"]:
            self.assertTrue(item["validation_repeat"])
            self.assertFalse(item["counts_toward_novel_fingerprint_gate"])
            self.assertFalse(item["accepted_update_allowed"])
            self.assertFalse(item["incumbent_update_allowed"])
            self.assertFalse(item["range_update_allowed"])
            self.assertFalse(item["gradient_update_allowed"])


if __name__ == "__main__":
    unittest.main()
