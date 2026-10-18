from __future__ import annotations

import unittest

from plan_boundary_resource_gate import select_resource_qualified_plan_boundaries


BASE_PARAMS = {"random_page_cost": 4.0}


def candidate(rank, value, cluster, distance_value, error=0.2, score=None):
    return {
        "rank": rank,
        "final_score": float(score if score is not None else 100 - rank),
        "optimized_params": {"random_page_cost": distance_value},
        "final_resource": {"cpu": 2.0, "memory": value, "io": 4.0},
        "final_estimation_metadata": {
            "workload_plan_fingerprint": cluster,
            "baseline_plan_fingerprint": "base",
            "resource_error_bounds": {
                "memory": {"absolute": error},
            },
        },
    }


class PlanBoundaryResourceGateTest(unittest.TestCase):
    def test_first_boundary_is_only_lower_bound_and_later_point_can_pass(self):
        selected, audit = select_resource_qualified_plan_boundaries(
            [
                candidate(1, 9.7, "p1", 3.8),
                candidate(2, 8.8, "p2", 3.0),
            ],
            incumbent_params=BASE_PARAMS,
            incumbent_resource={"memory": 10.0},
            target_resource="memory",
            max_distinct_clusters=5,
            min_target_improvement_pct=5.0,
        )
        self.assertEqual([item["rank"] for item in selected], [2])
        self.assertEqual(
            audit["checks"][0]["reason"],
            "conservative_improvement_below_target_threshold",
        )
        self.assertTrue(audit["checks"][0]["first_boundary_is_minimum_effective_step"])
        self.assertFalse(audit["qps_consulted"])

    def test_plan_scan_is_exhaustive_while_real_replays_are_capped(self):
        points = [
            candidate(index, 9.9, f"p{index}", 4.0 - index * 0.1)
            for index in range(1, 8)
        ]
        selected, audit = select_resource_qualified_plan_boundaries(
            points,
            incumbent_params=BASE_PARAMS,
            incumbent_resource={"memory": 10.0},
            target_resource="memory",
            max_distinct_clusters=5,
            min_target_improvement_pct=5.0,
        )
        self.assertEqual(selected, [])
        self.assertEqual(audit["available_distinct_cluster_count"], 7)
        self.assertEqual(audit["inspected_distinct_cluster_count"], 7)

    def test_only_first_five_resource_qualified_clusters_are_returned(self):
        points = [
            candidate(index, 8.0, f"p{index}", 4.0 - index * 0.1)
            for index in range(1, 8)
        ]
        selected, audit = select_resource_qualified_plan_boundaries(
            points,
            incumbent_params=BASE_PARAMS,
            incumbent_resource={"memory": 10.0},
            target_resource="memory",
            max_distinct_clusters=5,
            min_target_improvement_pct=5.0,
        )
        self.assertEqual(len(selected), 5)
        self.assertEqual(audit["available_distinct_cluster_count"], 7)
        self.assertEqual(audit["inspected_distinct_cluster_count"], 7)
        self.assertEqual([item["rank"] for item in selected], [1, 2, 3, 4, 5])

    def test_resource_score_not_distance_selects_replay_order(self):
        selected, audit = select_resource_qualified_plan_boundaries(
            [
                candidate(1, 8.0, "near", 3.9, score=1.0),
                candidate(2, 8.0, "far", 2.0, score=9.0),
            ],
            incumbent_params=BASE_PARAMS,
            incumbent_resource={"memory": 10.0},
            target_resource="memory",
        )
        self.assertEqual([item["rank"] for item in selected], [2, 1])
        self.assertEqual(
            audit["ranking_score"],
            "existing_resource_utility_final_score_desc",
        )

    def test_missing_error_bound_fails_closed(self):
        point = candidate(1, 8.0, "p1", 3.0)
        point["final_estimation_metadata"]["resource_error_bounds"] = {}
        selected, audit = select_resource_qualified_plan_boundaries(
            [point],
            incumbent_params=BASE_PARAMS,
            incumbent_resource={"memory": 10.0},
            target_resource="memory",
        )
        self.assertEqual(selected, [])
        self.assertEqual(audit["checks"][0]["reason"], "missing_historical_error_bound")


if __name__ == "__main__":
    unittest.main()
