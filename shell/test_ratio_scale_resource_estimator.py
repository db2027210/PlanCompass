from __future__ import annotations

import copy
import unittest

from plan_resource_estimator import (
    PlanResourceEstimator,
    rewrite_pgss_query_for_generic_explain,
)
from ratio_scale_resource_estimator import RatioScaleResourceEstimator


BASE_PARAMS = {
    "seq_page_cost": 1.0,
    "random_page_cost": 4.0,
    "cpu_tuple_cost": 0.01,
}


def seq_plan(rows=1000, pages=100):
    return {
        "Node Type": "Seq Scan",
        "Relation Name": "t",
        "Plan Rows": rows,
        "Plan Width": 32,
        "Startup Cost": 0.0,
        "Total Cost": float(pages),
    }


def index_plan(rows=10):
    return {
        "Node Type": "Index Scan",
        "Relation Name": "t",
        "Index Name": "t_idx",
        "Plan Rows": rows,
        "Plan Width": 32,
        "Startup Cost": 0.0,
        "Total Cost": 999999.0,
    }


def sort_plan(rows=1000):
    return {
        "Node Type": "Sort",
        "Plan Rows": rows,
        "Plan Width": 64,
        "Sort Key": ["t.id"],
        "Plans": [seq_plan(rows=rows)],
        "Startup Cost": 0.0,
        "Total Cost": float(rows),
    }


class FakeInner:
    def __init__(self, chooser=None):
        self.real = PlanResourceEstimator(
            pg_host="unused",
            pg_port="5432",
            pg_user="unused",
            pg_dbname="unused",
            psql_query_func=lambda _sql: "",
        )
        self.chooser = chooser or (lambda _params: seq_plan())

    def _collect_query_plans(self, queries, *, explain_workers, params=None):
        plan = self.chooser(params or {})
        return [
            {
                **copy.deepcopy(item),
                "queryid": item.get("queryid"),
                "query": item["query"],
                "replay_count": item.get("replay_count", 1),
                "plan_root": copy.deepcopy(plan),
            }
            for item in queries
        ]

    def _load_relation_stats(self):
        return {
            "t": {"relpages": 100.0, "reltuples": 1000.0},
            "t_idx": {"relpages": 10.0, "reltuples": 1000.0},
        }

    def _extract_plan_node_features(self, root, *, relation_stats=None):
        return self.real._extract_plan_node_features(root, relation_stats=relation_stats)


class LegacyInner:
    """Compatibility stub for lane-pinned estimators without relation stats."""

    def __init__(self):
        self.real = PlanResourceEstimator(
            pg_host="unused",
            pg_port="5432",
            pg_user="unused",
            pg_dbname="unused",
            psql_query_func=lambda _sql: "",
        )

    def _collect_query_plans(self, queries, *, explain_workers, params=None):
        return [
            {
                **copy.deepcopy(item),
                "queryid": item.get("queryid"),
                "query": item["query"],
                "replay_count": item.get("replay_count", 1),
                "plan_root": seq_plan(),
            }
            for item in queries
        ]

    def _extract_plan_node_features(self, root):
        return self.real._extract_plan_node_features(root)


def estimator(inner):
    return RatioScaleResourceEstimator(
        pg_host="unused",
        pg_port="5432",
        pg_user="unused",
        pg_dbname="unused",
        estimator_cfg={"ratio_scale": {"explain_workers": 1}},
        inner_estimator=inner,
    )


class RatioScaleResourceEstimatorTest(unittest.TestCase):
    def test_pgss_extract_field_is_rewritten_for_generic_explain(self):
        query = (
            "SELECT EXTRACT ( $1 FROM (o_orderdate + interval '1 day')) "
            "AS o_year FROM orders"
        )
        rewritten, audit = rewrite_pgss_query_for_generic_explain(query)
        self.assertIn(
            "date_part(CAST($1 AS text), (o_orderdate + interval '1 day'))",
            rewritten,
        )
        self.assertEqual(
            audit["rewrite_rules"],
            ["extract_parameter_field_to_date_part_text"],
        )
        self.assertTrue(audit["changed"])

    def test_pgss_rewriter_ignores_literals_comments_and_dollar_quotes(self):
        query = (
            "SELECT 'extract($1 from x)', $$extract($2 from y)$$, "
            "EXTRACT($10 FROM created_at) -- extract($3 from z)\n"
            "FROM events /* extract($4 from q) */"
        )
        rewritten, audit = rewrite_pgss_query_for_generic_explain(query)
        self.assertIn("'extract($1 from x)'", rewritten)
        self.assertIn("$$extract($2 from y)$$", rewritten)
        self.assertIn("-- extract($3 from z)", rewritten)
        self.assertIn("/* extract($4 from q) */", rewritten)
        self.assertIn("date_part(CAST($10 AS text), created_at)", rewritten)
        self.assertEqual(audit["rewrite_count"], 1)

    def test_pgss_sum_case_scalar_params_are_numeric(self):
        query = (
            "SELECT sum(CASE WHEN o_orderpriority = $1 THEN $2 "
            "ELSE $3 END) AS high_line_count FROM orders"
        )
        rewritten, audit = rewrite_pgss_query_for_generic_explain(query)
        self.assertIn("o_orderpriority = $1", rewritten)
        self.assertIn("THEN CAST($2 AS numeric)", rewritten)
        self.assertIn("ELSE CAST($3 AS numeric)", rewritten)
        self.assertEqual(
            audit["rewrite_rules"],
            [
                "sum_case_scalar_parameter_to_numeric",
                "sum_case_scalar_parameter_to_numeric",
            ],
        )

    def test_pgss_date_literal_and_ambiguous_addition_are_typed(self):
        query = (
            "SELECT * FROM lineitem WHERE l_shipdate >= date $6 "
            "AND l_quantity <= $8 + $9"
        )
        rewritten, audit = rewrite_pgss_query_for_generic_explain(query)
        self.assertIn("l_shipdate >= CAST($6 AS date)", rewritten)
        self.assertIn(
            "l_quantity <= CAST($8 AS numeric) + CAST($9 AS numeric)",
            rewritten,
        )
        self.assertEqual(
            audit["rewrite_rules"],
            [
                "date_literal_parameter_to_cast",
                "ambiguous_parameter_addition_to_numeric",
            ],
        )

    def test_pgss_date_column_duration_offset_is_typed(self):
        query = (
            "SELECT count(id) FROM LoanTrans "
            "WHERE date(contract_timestamp) + duration + $2 "
            "< cast($3 as date) and applicantID=$1"
        )
        rewritten, audit = rewrite_pgss_query_for_generic_explain(query)
        self.assertIn(
            "date(contract_timestamp) + duration + CAST($2 AS integer)",
            rewritten,
        )
        self.assertEqual(
            audit["rewrite_rules"],
            ["date_column_duration_offset_parameter_to_integer"],
        )

    def test_unsealed_custom_fallback_is_not_generated(self):
        inner = PlanResourceEstimator(
            pg_host="unused",
            pg_port="5432",
            pg_user="unused",
            pg_dbname="unused",
            psql_query_func=lambda _sql: "",
        )
        attempts = inner._build_explain_attempts(
            "SELECT * FROM t WHERE id = $1",
            mode="generic",
            queryid="unsealed",
        )
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0][1], "generic")
        self.assertNotIn(" = 1", attempts[0][0])

    def test_incomplete_explicit_custom_binding_fails_closed(self):
        inner = PlanResourceEstimator(
            pg_host="unused",
            pg_port="5432",
            pg_user="unused",
            pg_dbname="unused",
            custom_plan_params={"q": [1]},
            psql_query_func=lambda _sql: "",
        )
        with self.assertRaisesRegex(
            RuntimeError,
            "unsafe_custom_plan_binding_incomplete",
        ):
            inner._build_explain_attempts(
                "SELECT * FROM t WHERE a = $1 AND b = $2",
                mode="generic",
                queryid="q",
            )

    def test_legacy_inner_without_relation_stats_is_supported(self):
        subject = estimator(LegacyInner())
        baseline = {"cpu": 2.0, "memory": 3.0, "io": 4.0}
        subject.calibrate(["select * from t"], BASE_PARAMS, baseline)
        self.assertEqual(subject(["select * from t"], BASE_PARAMS), baseline)

    def test_baseline_identity_and_string_query_normalization(self):
        subject = estimator(FakeInner())
        baseline = {"cpu": 2.0, "memory": 3.0, "io": 4.0}
        subject.calibrate(["select * from t"], BASE_PARAMS, baseline)
        self.assertEqual(subject(["select * from t"], BASE_PARAMS), baseline)
        self.assertTrue(subject.last_metadata["cost_parameter_free_features"])

    def test_cost_values_do_not_change_features_when_plan_is_unchanged(self):
        subject = estimator(FakeInner())
        subject.calibrate(["select * from t"], BASE_PARAMS, {"cpu": 2, "memory": 3, "io": 4})
        candidate = dict(BASE_PARAMS, random_page_cost=99, cpu_tuple_cost=0.9)
        result, _stats, metadata = subject.estimate_with_metadata(["select * from t"], candidate)
        self.assertEqual(result, {"cpu": 2.0, "memory": 3.0, "io": 4.0})
        self.assertEqual(metadata["feature_ratios"], {"cpu": 1.0, "memory": 1.0, "io": 1.0})

    def test_plan_transition_changes_direction_without_using_cost_magnitude(self):
        inner = FakeInner(
            chooser=lambda params: index_plan() if params.get("random_page_cost", 4) < 2 else seq_plan()
        )
        subject = estimator(inner)
        subject.calibrate(["select * from t"], BASE_PARAMS, {"cpu": 2, "memory": 3, "io": 4})
        _result, _stats, metadata = subject.estimate_with_metadata(
            ["select * from t"], dict(BASE_PARAMS, random_page_cost=1.0)
        )
        self.assertLess(metadata["feature_ratios"]["cpu"], 1.0)
        self.assertLess(metadata["feature_ratios"]["io"], 1.0)
        self.assertNotEqual(
            metadata["baseline_plan_fingerprint"], metadata["workload_plan_fingerprint"]
        )

    def test_resource_specific_ratio_bounds_limit_cross_plan_extrapolation(self):
        inner = FakeInner(
            chooser=lambda params: index_plan()
            if params.get("random_page_cost", 4) < 2
            else seq_plan()
        )
        subject = RatioScaleResourceEstimator(
            pg_host="unused",
            pg_port="5432",
            pg_user="unused",
            pg_dbname="unused",
            estimator_cfg={
                "ratio_scale": {
                    "explain_workers": 1,
                    "ratio_min_by_resource": {
                        "cpu": 0.8,
                        "memory": 0.85,
                        "io": 0.9,
                    },
                    "ratio_max_by_resource": {
                        "cpu": 1.2,
                        "memory": 1.15,
                        "io": 1.1,
                    },
                }
            },
            inner_estimator=inner,
        )
        baseline = {"cpu": 2.0, "memory": 3.0, "io": 4.0}
        subject.calibrate(["select * from t"], BASE_PARAMS, baseline)
        result, _stats, metadata = subject.estimate_with_metadata(
            ["select * from t"],
            dict(BASE_PARAMS, random_page_cost=1.0),
        )
        self.assertLess(metadata["feature_ratios_unclipped"]["cpu"], 0.8)
        self.assertLess(metadata["feature_ratios_unclipped"]["io"], 0.9)
        self.assertEqual(metadata["feature_ratios"]["cpu"], 0.8)
        self.assertEqual(metadata["feature_ratios"]["io"], 0.9)
        self.assertAlmostEqual(result["cpu"], baseline["cpu"] * 0.8)
        self.assertAlmostEqual(result["io"], baseline["io"] * 0.9)
        self.assertEqual(
            metadata["ratio_clip_by_resource"]["cpu"],
            [0.8, 1.2],
        )

    def test_replay_count_weights_structural_features(self):
        subject = estimator(FakeInner())
        one = subject._compute_structural_features(
            subject._normalize_queries([{"query": "select * from t", "replay_count": 1}]),
            BASE_PARAMS,
        )
        three = subject._compute_structural_features(
            subject._normalize_queries([{"query": "select * from t", "replay_count": 3}]),
            BASE_PARAMS,
        )
        self.assertAlmostEqual(three["cpu_total"], one["cpu_total"] * 3)
        self.assertAlmostEqual(three["io_total"], one["io_total"] * 3)
        self.assertAlmostEqual(three["memory_total"], one["memory_total"])

    def test_memory_fixed_floor_is_not_scaled_by_plan_change(self):
        inner = FakeInner(
            chooser=lambda params: sort_plan(
                2000 if params.get("random_page_cost", 4) < 2 else 1000
            )
        )
        subject = RatioScaleResourceEstimator(
            pg_host="unused",
            pg_port="5432",
            pg_user="unused",
            pg_dbname="unused",
            estimator_cfg={
                "ratio_scale": {
                    "explain_workers": 1,
                    "require_memory_floor": True,
                    "memory_fixed_baseline": 8.0,
                }
            },
            inner_estimator=inner,
        )
        subject.calibrate(
            [{"query": "select * from t", "mean_time_ms": 10.0}],
            BASE_PARAMS,
            {"cpu": 2, "memory": 10, "io": 4},
        )
        result, _stats, metadata = subject.estimate_with_metadata(
            [{"query": "select * from t", "mean_time_ms": 10.0}],
            dict(BASE_PARAMS, random_page_cost=1.0),
        )
        self.assertAlmostEqual(
            metadata["structural_feature_ratios_unclipped"]["memory"],
            2.0,
        )
        self.assertAlmostEqual(metadata["feature_ratios"]["memory"], 1.2)
        self.assertAlmostEqual(result["memory"], 12.0)
        self.assertAlmostEqual(
            result["memory"],
            10.0 * metadata["feature_ratios"]["memory"],
        )
        self.assertEqual(metadata["memory_prediction"]["fixed"], 8.0)

    def test_historical_error_bounds_are_exposed_without_qps_scoring(self):
        subject = RatioScaleResourceEstimator(
            pg_host="unused",
            pg_port="5432",
            pg_user="unused",
            pg_dbname="unused",
            estimator_cfg={
                "ratio_scale": {
                    "explain_workers": 1,
                    "historical_error_bounds": {
                        "memory": {"relative_pct": 2.0, "absolute": 0.1}
                    },
                }
            },
            inner_estimator=FakeInner(),
        )
        subject.calibrate(
            [{"query": "select * from t", "mean_time_ms": 10.0}],
            BASE_PARAMS,
            {"cpu": 2, "memory": 10, "io": 4},
        )
        _result, _stats, metadata = subject.estimate_with_metadata(
            [{"query": "select * from t", "mean_time_ms": 10.0}],
            BASE_PARAMS,
        )
        self.assertAlmostEqual(
            metadata["resource_error_bounds"]["memory"]["absolute"],
            0.2,
        )
        self.assertFalse(metadata["qps_used_for_inner_scoring"])

    def test_required_memory_latency_fails_closed(self):
        subject = RatioScaleResourceEstimator(
            pg_host="unused",
            pg_port="5432",
            pg_user="unused",
            pg_dbname="unused",
            estimator_cfg={
                "ratio_scale": {
                    "explain_workers": 1,
                    "require_query_latency_for_memory": True,
                    "allow_resource_only_memory_fallback": False,
                }
            },
            inner_estimator=FakeInner(),
        )
        with self.assertRaisesRegex(RuntimeError, "query-level latency"):
            subject.calibrate(
                ["select * from t"],
                BASE_PARAMS,
                {"cpu": 2, "memory": 10, "io": 4},
            )

    def test_required_memory_latency_uses_resource_only_fallback(self):
        subject = RatioScaleResourceEstimator(
            pg_host="unused",
            pg_port="5432",
            pg_user="unused",
            pg_dbname="unused",
            estimator_cfg={
                "ratio_scale": {
                    "explain_workers": 1,
                    "require_query_latency_for_memory": True,
                    "allow_resource_only_memory_fallback": True,
                }
            },
            inner_estimator=FakeInner(),
        )
        calibration = subject.calibrate(
            [{"query": "select * from t"}],
            BASE_PARAMS,
            {"cpu": 2, "memory": 10, "io": 4},
        )
        fallback = calibration["memory_ratio_scale_skipped_missing_latency"]
        self.assertEqual(fallback["missing"], 1)
        self.assertEqual(fallback["total"], 1)
        self.assertFalse(fallback["qps_used"])
        self.assertFalse(fallback["latency_used"])
        _result, _stats, metadata = subject.estimate_with_metadata(
            [{"query": "select * from t"}], BASE_PARAMS
        )
        self.assertEqual(
            metadata["memory_ratio_scale_skipped_missing_latency"],
            fallback,
        )

    def test_missing_or_nonpositive_baseline_fails_closed(self):
        subject = estimator(FakeInner())
        with self.assertRaisesRegex(ValueError, "baseline_real.memory"):
            subject.calibrate(
                ["select * from t"], BASE_PARAMS, {"cpu": 2, "memory": 0, "io": 4}
            )

    def test_estimation_before_calibration_fails_closed(self):
        subject = estimator(FakeInner())
        with self.assertRaisesRegex(RuntimeError, "calibrate"):
            subject(["select * from t"], BASE_PARAMS)

    def test_distinct_heap_pages_is_bounded(self):
        self.assertGreaterEqual(
            RatioScaleResourceEstimator._distinct_heap_pages(10, 100, 1000), 1
        )
        self.assertLessEqual(
            RatioScaleResourceEstimator._distinct_heap_pages(100000, 100, 1000), 100
        )


if __name__ == "__main__":
    unittest.main()
