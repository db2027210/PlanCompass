from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from mysql_parameter_optimizer import (
    REQUIRED_REAL_REPLAY_EVIDENCE,
    _advance_rejected_escape_state,
    _evaluate_outer_eligibility,
    _gradient_replay_stage_action,
    _online_update_experience_table_and_rerank,
    _resource_only_post_top5_feedback,
    validate_config,
)
from mysql_plan_clustering import formal_replay_cap


class MySQLFormalAlgorithmBContractTest(unittest.TestCase):
    def valid_measurement(self):
        return {
            "qps": 101.0,
            "db_qps": 101.0,
            "db_qps_source": "database_status_counter",
            "correctness_passed": True,
            "error_rate": 0.0,
            "stable": True,
            "sample_coverage": 1.0,
            "oom": False,
            "mysql_restart": False,
            "container_restart": False,
            "p95_ms": 10.0,
            "p99_ms": 20.0,
            "utilization_pct": {"cpu": 0.79, "memory": 0.70, "io": 0.79},
            "three_level_fingerprint": {
                "true_duplicate_usable": True,
                "qps_in_fingerprint": False,
                "l1": {"sha256": "a" * 64},
                "l2": {"sha256": "b" * 64},
                "l3": {"sha256": "c" * 64},
            },
            "evidence": {
                name: f"artifact://{name}"
                for name in REQUIRED_REAL_REPLAY_EVIDENCE
            },
        }

    def test_one_global_fixed_top5_batch(self):
        for benchmark in ("job", "hybench_xp", "tpcc", "tpch"):
            self.assertEqual(formal_replay_cap(benchmark), 5)
        self.assertEqual(
            _gradient_replay_stage_action(
                validated_count=5,
                eligible_count=0,
                available_count=20,
                initial_top_k=5,
                max_top_k=5,
            ),
            "reject_exploration",
        )
        with self.assertRaisesRegex(ValueError, "fixed Top-5"):
            _gradient_replay_stage_action(
                validated_count=5,
                eligible_count=0,
                available_count=20,
                initial_top_k=5,
                max_top_k=10,
            )

    def test_qps_cannot_change_online_resource_update_or_ranking(self):
        sensitivity = {
            "p": {
                "cpu": 0.1,
                "memory": 0.2,
                "io": 0.3,
                "confidence": 1.0,
                "evidence_count": 0.0,
            }
        }
        baseline_params = {"p": 1.0}
        baseline_validation = {
            "db_qps": 100.0,
            "utilization_pct": {"cpu": 0.8, "memory": 0.9, "io": 0.7},
        }
        candidates = [
            {"rank": 1, "subset_id": "a", "optimized_params": {"p": 0.75}},
            {"rank": 2, "subset_id": "b", "optimized_params": {"p": 1.75}},
        ]

        def evaluations(qps_values):
            rows = []
            for index, (parameter, qps) in enumerate(
                zip((0.5, 1.5, 2.0), qps_values), start=1
            ):
                rows.append(
                    {
                        "candidate_optimized_params": {"p": parameter},
                        "candidate_final_validation": {
                            "db_qps": qps,
                            "qps": qps,
                            "utilization_pct": {
                                "cpu": 0.75 + 0.02 * index,
                                "memory": 0.95 - 0.03 * index,
                                "io": 0.65 + 0.01 * index,
                            },
                        },
                    }
                )
            return rows

        low = _online_update_experience_table_and_rerank(
            sensitivity,
            baseline_params=baseline_params,
            baseline_validation=baseline_validation,
            evaluations=evaluations((1.0, 2.0, 3.0)),
            remaining_candidates=candidates,
            target_resource="memory",
        )
        high = _online_update_experience_table_and_rerank(
            sensitivity,
            baseline_params=baseline_params,
            baseline_validation=baseline_validation,
            evaluations=evaluations((300.0, 200.0, 100.0)),
            remaining_candidates=candidates,
            target_resource="memory",
        )

        self.assertEqual(low, high)

    def test_candidate_gate_uses_non_target_rise_not_legacy_0_8_cap(self):
        baseline = {
            "qps": 100.0,
            "db_qps": 100.0,
            "p95_ms": 10.0,
            "p99_ms": 20.0,
            "utilization_pct": {"cpu": 0.40, "memory": 0.90, "io": 0.40},
        }
        measurement = self.valid_measurement()
        accepted = _evaluate_outer_eligibility(
            measurement,
            baseline=baseline,
            target_resource="memory",
            benchmark="job",
        )
        self.assertTrue(accepted["eligible"])

        measurement["utilization_pct"]["cpu"] = 0.80
        still_accepted = _evaluate_outer_eligibility(
            measurement,
            baseline=baseline,
            target_resource="memory",
            benchmark="job",
        )
        self.assertTrue(still_accepted["eligible"])
        self.assertFalse(
            still_accepted["checks"]["non_target_resources_strictly_below_0_8"]
        )
        self.assertTrue(
            still_accepted["checks"]["all_non_target_resources_strictly_increased"]
        )

    def test_rejected_top5_can_reverse_only_resource_direction(self):
        sensitivity = {
            "p": {"cpu": 0.1, "memory": -0.2, "io": 0.1}
        }
        baseline = {
            "qps": 100.0,
            "db_qps": 100.0,
            "utilization_pct": {"cpu": 0.4, "memory": 0.9, "io": 0.4},
        }

        def evaluations(qps_values):
            rows = []
            for ordinal, (parameter, memory, qps) in enumerate(
                zip((1.2, 1.4), (0.94, 0.95), qps_values), start=1
            ):
                rows.append({
                    "replay_ordinal": ordinal,
                    "candidate_subset_id": f"single-{ordinal}",
                    "candidate_optimized_params": {"p": parameter},
                    "candidate_predicted_initial_resource": {
                        "cpu": 40.0, "memory": 90.0, "io": 40.0,
                    },
                    "candidate_predicted_final_resource": {
                        "cpu": 40.0, "memory": 85.0, "io": 40.0,
                    },
                    "candidate_final_validation": {
                        "qps": qps,
                        "db_qps": qps,
                        "stable": True,
                        "correctness_passed": True,
                        "oom": False,
                        "mysql_restart": False,
                        "container_restart": False,
                        "utilization_pct": {
                            "cpu": 0.4, "memory": memory, "io": 0.4,
                        },
                    },
                    "recovery": {"verified": True},
                })
            return rows

        low = _resource_only_post_top5_feedback(
            sensitivity,
            baseline_params={"p": 1.0},
            baseline_validation=baseline,
            evaluations=evaluations((1.0, 2.0)),
        )
        high = _resource_only_post_top5_feedback(
            sensitivity,
            baseline_params={"p": 1.0},
            baseline_validation=baseline,
            evaluations=evaluations((200.0, 100.0)),
        )
        self.assertEqual(low, high)
        updated, audit = low
        self.assertEqual(updated["p"]["memory"], 0.2)
        self.assertEqual(updated["p"]["cpu"], 0.1)
        self.assertEqual(updated["p"]["io"], 0.1)
        self.assertTrue(audit["enabled"])
        self.assertFalse(audit["qps_fields_read"])
        self.assertEqual(audit["next_outer_eta_scale"], 0.5)

    def test_rejection_expands_escape_radius_and_caps_at_five_batches(self):
        first = _advance_rejected_escape_state(
            current_radius=0.08,
            maximum_radius=0.32,
            radius_gamma=0.5,
            current_eta=0.005,
            minimum_eta=0.0005,
            consecutive_rejected_batches=0,
            maximum_rejected_batches=5,
            resource_feedback={"enabled": True, "next_outer_eta_scale": 0.5},
        )
        self.assertEqual(first["escape_radius"], 0.16)
        self.assertEqual(first["eta"], 0.0025)
        self.assertFalse(first["exhausted"])
        fifth = _advance_rejected_escape_state(
            current_radius=0.32,
            maximum_radius=0.32,
            radius_gamma=0.5,
            current_eta=0.0005,
            minimum_eta=0.0005,
            consecutive_rejected_batches=4,
            maximum_rejected_batches=5,
            resource_feedback={"enabled": False},
        )
        self.assertEqual(fifth["escape_radius"], 0.32)
        self.assertEqual(fifth["eta"], 0.0005)
        self.assertTrue(fifth["exhausted"])
        self.assertFalse(fifth["qps_fields_read"])

    def _v3_config(self, registry_path: Path, registry_sha256: str):
        active = [
            "io_block_read_cost",
            "memory_block_read_cost",
            "row_evaluate_cost",
            "memory_temptable_row_cost",
        ]
        ranges = {
            "io_block_read_cost": {"min_value": 0.1, "max_value": 10.0},
            "memory_block_read_cost": {"min_value": 0.025, "max_value": 2.5},
            "row_evaluate_cost": {"min_value": 0.01, "max_value": 1.0},
            "memory_temptable_row_cost": {"min_value": 0.01, "max_value": 1.0},
        }
        return {
            "formal_scope": "mysql_only",
            "freeze_id": "frozen_mysql_workloads_and_parameters_20260723_v3",
            "benchmark": "job",
            "capability": {"cpu": 1.0, "memory": 1.0, "io": 1.0},
            "cost_parameters": {
                "active": active,
                "ranges": ranges,
                "baseline_values": {
                    "io_block_read_cost": 1.0,
                    "memory_block_read_cost": 0.25,
                    "row_evaluate_cost": 0.1,
                    "memory_temptable_row_cost": 0.1,
                },
            },
            "estimator": {
                "type": "ratio_scale",
                "baseline_real": {"cpu": 0.4, "memory": 0.9, "io": 0.4},
                "feature_source": {
                    "registry_query_ids": ["job_1a"],
                    "workload_registry_sha256": registry_sha256,
                    "workload_registry_path": str(registry_path),
                    "require_strict_plan_registry": True,
                    "workload_freeze_id": (
                        "frozen_mysql_workloads_and_parameters_20260723_v3"
                    ),
                    "benchmark_id": "job",
                },
            },
            "optimizer": {
                "candidate_generator": (
                    "all_nonempty_subsets_experience_table_analytic_sgd_v1"
                ),
                "subset_max_workers": 1,
            },
            "formal_outer_protocol": {
                "target_accepted_shrinks": 10,
                "max_outer_attempts": 10,
                "formal_search_initial_replay_top_k": 5,
                "formal_search_max_replay_top_k": 5,
            },
        }

    def test_v3_freeze_requires_authoritative_registry(self):
        with tempfile.TemporaryDirectory() as raw:
            registry = Path(raw) / "job.registry.json"
            registry.write_text(json.dumps({
                "benchmark": "job",
                "freeze_id": "frozen_mysql_workloads_and_parameters_20260723_v3",
                "immutable": True,
                "queries": [{
                    "query_id": "job_1a",
                    "dynamic": False,
                    "representative_bindings": [],
                }],
            }), encoding="utf-8")
            digest = hashlib.sha256(registry.read_bytes()).hexdigest()
            validate_config(self._v3_config(registry, digest))

            payload = json.loads(registry.read_text(encoding="utf-8"))
            payload["immutable"] = False
            payload["execution_authorized"] = False
            registry.write_text(json.dumps(payload), encoding="utf-8")
            digest = hashlib.sha256(registry.read_bytes()).hexdigest()
            with self.assertRaisesRegex(ValueError, "execution is not authorized"):
                validate_config(self._v3_config(registry, digest))

    def test_unknown_formal_freeze_is_rejected(self):
        config = {
            "formal_scope": "mysql_only",
            "freeze_id": "unregistered-freeze",
            "capability": {"cpu": 1.0, "memory": 1.0, "io": 1.0},
            "cost_parameters": {"active": ["row_evaluate_cost"]},
        }
        with self.assertRaisesRegex(ValueError, "registered freeze_id"):
            validate_config(config)


if __name__ == "__main__":
    unittest.main()
