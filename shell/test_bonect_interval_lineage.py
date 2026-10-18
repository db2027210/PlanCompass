#!/usr/bin/env python3
from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path
from typing import Any, Dict, List


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from binary_plan_resource_tuner import (  # noqa: E402
    candidate_mask_values,
    update_ranges_around_best,
)
from bonect_interval_lineage import (  # noqa: E402
    COST_PARAMS,
    IntervalInvariantError,
    audit_frozen_round_holdout,
    candidate_gate,
    canonical_sha256,
    dedupe_plan_resource_candidates,
    freeze_round_manifest,
    interval_metrics,
    interval_midpoint,
    interval_pair_points,
    make_root_manifest,
    verify_superior_substitute,
)
from shell.bonect_full_grid_estimator_scan import (  # noqa: E402
    PsycopgConnection,
    build_explained_queries,
)


ROOT_BOUNDS = {
    "seq_page_cost": [1e-4, 100.0],
    "random_page_cost": [1e-4, 20.0],
    "cpu_tuple_cost": [1e-6, 1.0],
    "cpu_index_tuple_cost": [1e-6, 1.0],
    "cpu_operator_cost": [1e-6, 1.0],
    "parallel_setup_cost": [1.0, 10000.0],
    "parallel_tuple_cost": [1e-6, 10.0],
}

FAR_DEFAULT = {
    "seq_page_cost": 1.0,
    "random_page_cost": 4.0,
    "cpu_tuple_cost": 0.01,
    "cpu_index_tuple_cost": 0.005,
    "cpu_operator_cost": 0.0025,
    "parallel_setup_cost": 1000.0,
    "parallel_tuple_cost": 0.1,
}

TEACHER = {
    "seq_page_cost": 62.5000375,
    "random_page_cost": 12.500037500000001,
    "cpu_tuple_cost": 0.375000625,
    "cpu_index_tuple_cost": 0.12500087499999998,
    "cpu_operator_cost": 0.12500087499999998,
    "parallel_setup_cost": 8750.0,
    "parallel_tuple_cost": 8.750000125,
}


def log_point(bounds: Dict[str, List[float]], fraction: float) -> Dict[str, float]:
    return {
        name: math.exp(math.log(bounds[name][0]) + fraction * (math.log(bounds[name][1]) - math.log(bounds[name][0])))
        for name in COST_PARAMS
    }


def contracted(bounds: Dict[str, List[float]], fraction: float = 0.05) -> Dict[str, List[float]]:
    return {
        name: [
            math.exp(math.log(bounds[name][0]) + fraction * (math.log(bounds[name][1]) - math.log(bounds[name][0]))),
            bounds[name][1],
        ]
        for name in COST_PARAMS
    }


def frozen_round(
    parent: Dict[str, Any],
    round_index: int,
    bounds: Dict[str, List[float]],
) -> Dict[str, Any]:
    rows = []
    predictions = []
    for index, fraction in enumerate((0.55, 0.82), start=1):
        candidate_id = f"anonymous_r{round_index}_{index}"
        params = log_point(bounds, fraction)
        rows.append(
            {
                "candidate_id": candidate_id,
                "params": params,
                "effective_params": params,
            }
        )
        predictions.append(
            {
                "candidate_id": candidate_id,
                "params": params,
                "effective_params": params,
                "plan_hashes": {"q": "basin-a" if index == 1 else "basin-b"},
                "predicted_resources": {"cpu": 20.0, "memory": 30.0, "io": 76.0},
                "rank_score": 10.0 - index,
                "uncertainty": 1.0,
                "candidate_gate": {"passed": True},
                "plan_features": {
                    "category_counts": {"Scan": index, "Join": 2},
                    "parallel_aware_node_count": index,
                    "gather_workers_planned_sum": index * 2,
                    "spill_risk_node_count_16mb_proxy": 1,
                },
            }
        )
    return freeze_round_manifest(
        round_index=round_index,
        requested_bounds=bounds,
        effective_bounds=bounds,
        parent_manifest=parent,
        parent_feedback={
            "parent_round": parent["round"],
            "parent_interval_sha": parent["interval_manifest_sha256"],
            "feedback_sha256": canonical_sha256({"round": parent["round"], "far_default": FAR_DEFAULT}),
        },
        candidates=rows,
        ranked_predictions=predictions,
        topk_candidate_ids=[predictions[0]["candidate_id"]],
        generation_provenance={
            "start": "far_default",
            "holdout_loaded_before_freeze": False,
            "controller_inputs": ["public bounds", "non-teacher synthetic feedback"],
        },
    )


class IntervalLineageTests(unittest.TestCase):
    def test_psycopg_connection_serializes_json_column(self) -> None:
        class Cursor:
            def __enter__(self) -> "Cursor":
                return self

            def __exit__(self, *args: Any) -> None:
                _ = args

            def execute(self, sql: str) -> None:
                _ = sql

            def fetchall(self) -> List[Any]:
                return [([{"Plan": {"Node Type": "Result"}}],)]

        class Connection:
            @staticmethod
            def cursor() -> Cursor:
                return Cursor()

        wrapper = object.__new__(PsycopgConnection)
        wrapper.conn = Connection()
        raw = wrapper.execute("EXPLAIN SELECT 1")
        self.assertIn('"Plan"', raw)
        self.assertNotIn("'Plan'", raw)

    def test_plan_capture_keeps_explain_as_only_row_result(self) -> None:
        class FakeEstimator:
            plan_capture_modes = ["generic"]

            @staticmethod
            def _build_explain_attempts(query: str, mode: str, queryid: str) -> List[Any]:
                _ = query, mode, queryid
                return [("EXPLAIN (FORMAT JSON) SELECT 1", "generic")]

            @staticmethod
            def _extract_explain_json(raw: str) -> Any:
                self.assertNotEqual(raw, "")
                return [{"Plan": {"Node Type": "Result", "Total Cost": 0.01}}]

        class FakeConnection:
            def __init__(self) -> None:
                self.calls: List[str] = []

            def execute(self, sql: str, *, timeout_s: float) -> str:
                _ = timeout_s
                self.calls.append(sql.strip())
                if sql.strip().startswith("EXPLAIN"):
                    return '[{"Plan":{"Node Type":"Result","Total Cost":0.01}}]'
                return ""

        conn = FakeConnection()
        explained = build_explained_queries(
            FakeEstimator(),
            conn,
            [{"queryid": "q", "query": "SELECT 1", "replay_count": 1}],
            FAR_DEFAULT,
        )
        self.assertFalse(explained[0].get("error"))
        self.assertEqual(conn.calls[-4:], [
            "SAVEPOINT scan_query;",
            "EXPLAIN (FORMAT JSON) SELECT 1;",
            "ROLLBACK TO SAVEPOINT scan_query;",
            "ROLLBACK;",
        ])

    def test_log_midpoint_and_quarters(self) -> None:
        self.assertAlmostEqual(interval_midpoint(1e-6, 1.0), 0.001)
        lower, upper = interval_pair_points(1e-6, 1.0)
        self.assertAlmostEqual(lower, 10 ** -4.5)
        self.assertAlmostEqual(upper, 10 ** -1.5)
        self.assertEqual(interval_midpoint(0.0, 10.0), 5.0)

    def test_tuner_uses_log_space(self) -> None:
        ranges = {name: (1e-6, 1.0) for name in COST_PARAMS}
        values = candidate_mask_values([COST_PARAMS[0]], ranges)
        self.assertAlmostEqual(values[0][1][COST_PARAMS[0]], 10 ** -4.5)
        updated = update_ranges_around_best(ranges, [COST_PARAMS[0]], {COST_PARAMS[0]: 0.1})
        self.assertAlmostEqual(updated[COST_PARAMS[0]][0], 0.001)
        self.assertEqual(updated[COST_PARAMS[0]][1], 1.0)

    def test_candidate_gate_io_drop_sign(self) -> None:
        baseline = {"cpu": 12.0, "memory": 26.0, "io": 85.0}
        good = candidate_gate(
            baseline,
            {
                "cpu": 18.0,
                "memory": 25.0,
                "io": 77.0,
                "db_qps_gain_lcb_pct": 16.0,
                "db_qps_gain_ucb_pct": 25.0,
            },
        )
        bad = candidate_gate(
            baseline,
            {
                "cpu": 18.0,
                "memory": 25.0,
                "io": 90.0,
                "db_qps_gain_lcb_pct": 16.0,
                "db_qps_gain_ucb_pct": 25.0,
            },
        )
        self.assertTrue(good["passed"])
        self.assertEqual(good["io_drop_pp"], 8.0)
        self.assertFalse(bad["passed"])
        self.assertEqual(bad["io_drop_pp"], -5.0)

    def test_plan_resource_dedupe(self) -> None:
        base = {
            "plan_hashes": {"q": "same"},
            "predicted_resources": {"cpu": 10.1, "memory": 20.1, "io": 70.1},
            "uncertainty": 1.0,
        }
        kept, dropped = dedupe_plan_resource_candidates(
            [
                {**base, "candidate_id": "a", "rank_score": 2.0},
                {**base, "candidate_id": "b", "rank_score": 1.0},
            ]
        )
        self.assertEqual([row["candidate_id"] for row in kept], ["a"])
        self.assertEqual(dropped[0]["duplicate_of"], "a")

    def test_three_round_far_default_freeze_then_holdout(self) -> None:
        parent = make_root_manifest(ROOT_BOUNDS, source="public bounds and far default")
        bounds = ROOT_BOUNDS
        ratios = []
        for round_index in range(1, 4):
            bounds = contracted(bounds)
            manifest = frozen_round(parent, round_index, bounds)
            candidate_after = manifest["candidates"]
            order_after = [row["candidate_id"] for row in manifest["ranked_predictions"]]
            receipt = audit_frozen_round_holdout(
                manifest=manifest,
                parent_manifest=parent,
                teacher_params=TEACHER,
                candidate_sha_after_holdout=canonical_sha256(candidate_after),
                candidate_order_sha_after_holdout=canonical_sha256(order_after),
                teacher_plan_hashes={"q": "basin-a"},
                teacher_plan_features={
                    "category_counts": {"Scan": 1, "Join": 2},
                    "parallel_aware_node_count": 1,
                    "gather_workers_planned_sum": 2,
                    "spill_risk_node_count_16mb_proxy": 1,
                },
            )
            self.assertTrue(receipt["teacher_contained"])
            self.assertTrue(receipt["strict_contraction"])
            self.assertTrue(receipt["no_teacher_leakage"])
            self.assertTrue(receipt["outer_run_allowed"])
            self.assertEqual(receipt["teacher_equivalent_plan_basin_best_rank"], 1)
            self.assertEqual(receipt["teacher_plan_family_nearest"]["rank"], 1)
            self.assertEqual(receipt["teacher_plan_family_nearest"]["node_signature_l1_distance"], 0.0)
            ratios.append(receipt["shrink_ratio"])
            parent = manifest
        self.assertTrue(all(0.0 < ratio < 1.0 for ratio in ratios))

    def test_wrong_parent_and_interval_mismatch_fail_closed(self) -> None:
        root = make_root_manifest(ROOT_BOUNDS, source="test")
        bounds = contracted(ROOT_BOUNDS)
        with self.assertRaisesRegex(IntervalInvariantError, "wrong_parent_feedback"):
            freeze_round_manifest(
                round_index=1,
                requested_bounds=bounds,
                effective_bounds=bounds,
                parent_manifest=root,
                parent_feedback={"parent_round": 0, "parent_interval_sha": "wrong"},
                candidates=[],
                ranked_predictions=[],
                topk_candidate_ids=["x"],
                generation_provenance={"holdout_loaded_before_freeze": False},
            )
        bad = frozen_round(root, 1, bounds)
        bad_candidate = dict(bad["candidates"][0])
        bad_candidate["params"] = dict(bad_candidate["params"])
        bad_candidate["params"][COST_PARAMS[0]] = ROOT_BOUNDS[COST_PARAMS[0]][0]
        with self.assertRaisesRegex(IntervalInvariantError, "interval_candidate_mismatch"):
            freeze_round_manifest(
                round_index=1,
                requested_bounds=bounds,
                effective_bounds=bounds,
                parent_manifest=root,
                parent_feedback={
                    "parent_round": 0,
                    "parent_interval_sha": root["interval_manifest_sha256"],
                },
                candidates=[bad_candidate],
                ranked_predictions=[
                    {"candidate_id": bad_candidate["candidate_id"], "rank_score": 1.0}
                ],
                topk_candidate_ids=[bad_candidate["candidate_id"]],
                generation_provenance={"holdout_loaded_before_freeze": False},
            )

    def test_effective_rounding_has_separate_interval(self) -> None:
        root = make_root_manifest(ROOT_BOUNDS, source="test", effective_bounds=ROOT_BOUNDS)
        requested = contracted(ROOT_BOUNDS, 0.05)
        effective = {
            name: [requested[name][0] * 0.999999, requested[name][1]]
            for name in COST_PARAMS
        }
        params = log_point(requested, 0.6)
        effective_params = {name: value * 1.0000001 for name, value in params.items()}
        manifest = freeze_round_manifest(
            round_index=1,
            requested_bounds=requested,
            effective_bounds=effective,
            parent_manifest=root,
            parent_feedback={
                "parent_round": 0,
                "parent_interval_sha": root["interval_manifest_sha256"],
            },
            candidates=[{"candidate_id": "rounded", "params": params, "effective_params": effective_params}],
            ranked_predictions=[
                {"candidate_id": "rounded", "rank_score": 1.0, "candidate_gate": {"passed": True}}
            ],
            topk_candidate_ids=["rounded"],
            generation_provenance={"holdout_loaded_before_freeze": False},
        )
        self.assertTrue(manifest["effective_interval_metrics"]["strictly_contracted"])

    def test_teacher_leakage_and_not_contracted_are_blocked(self) -> None:
        root = make_root_manifest(ROOT_BOUNDS, source="test")
        with self.assertRaisesRegex(IntervalInvariantError, "not_strictly_contracted"):
            frozen_round(root, 1, ROOT_BOUNDS)
        bounds = contracted(ROOT_BOUNDS)
        manifest = frozen_round(root, 1, bounds)
        receipt = audit_frozen_round_holdout(
            manifest=manifest,
            parent_manifest=root,
            teacher_params=TEACHER,
            candidate_sha_after_holdout="changed",
            candidate_order_sha_after_holdout=manifest["candidate_order_sha256"],
        )
        self.assertFalse(receipt["outer_run_allowed"])
        self.assertIn("interval_candidate_mismatch", receipt["fail_closed_reasons"])
        self.assertIn("teacher_leakage", receipt["fail_closed_reasons"])

    def test_superior_substitute_requires_formal_same_environment(self) -> None:
        evidence = {
            "teacher_same_environment": {
                "environment_fingerprint": "env",
                "db_qps_gain_pct": 20.0,
                "io_drop_pp": 6.0,
                "final_io_pct": 77.0,
            },
            "candidate": {
                "environment_fingerprint": "env",
                "measurement_class": "isolated_5m_formal",
                "strong_gate_passed": True,
                "db_qps_gain_pct": 22.0,
                "io_drop_pp": 6.0,
                "final_io_pct": 77.0,
                "cpu_pct": 25.0,
                "memory_pct": 35.0,
                "cpu_delta_pp": 3.0,
                "memory_delta_pp": -1.0,
            },
        }
        self.assertTrue(verify_superior_substitute(evidence)["verified_same_environment_strict_superior"])
        evidence["candidate"]["measurement_class"] = "parallel_exploratory_2m"
        self.assertFalse(verify_superior_substitute(evidence)["verified_same_environment_strict_superior"])

    def test_outer_is_blocked_when_no_candidate_passes_gate(self) -> None:
        root = make_root_manifest(ROOT_BOUNDS, source="test")
        bounds = contracted(ROOT_BOUNDS)
        params = log_point(bounds, 0.7)
        manifest = freeze_round_manifest(
            round_index=1,
            requested_bounds=bounds,
            effective_bounds=bounds,
            parent_manifest=root,
            parent_feedback={
                "parent_round": 0,
                "parent_interval_sha": root["interval_manifest_sha256"],
            },
            candidates=[{"candidate_id": "miss", "params": params, "effective_params": params}],
            ranked_predictions=[
                {"candidate_id": "miss", "rank_score": 1.0, "candidate_gate": {"passed": False}}
            ],
            topk_candidate_ids=[],
            generation_provenance={"holdout_loaded_before_freeze": False},
        )
        receipt = audit_frozen_round_holdout(
            manifest=manifest,
            parent_manifest=root,
            teacher_params=TEACHER,
            candidate_sha_after_holdout=manifest["candidate_sha256"],
            candidate_order_sha_after_holdout=manifest["candidate_order_sha256"],
        )
        self.assertTrue(receipt["interval_invariants_passed"])
        self.assertFalse(receipt["outer_run_allowed"])
        self.assertIn("candidate_gate_failed", receipt["fail_closed_reasons"])

    def test_only_seven_keys(self) -> None:
        self.assertEqual(len(COST_PARAMS), 7)
        self.assertEqual(set(FAR_DEFAULT), set(COST_PARAMS))
        metrics = interval_metrics(contracted(ROOT_BOUNDS), ROOT_BOUNDS)
        self.assertTrue(metrics["strictly_contracted"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
