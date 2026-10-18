from __future__ import annotations

import unittest

from mysql_parameter_optimizer import (
    REQUIRED_REAL_REPLAY_EVIDENCE,
    _orchestrate_staged_real_replays,
    _plan_tree_extension_schedule,
)
from mysql_plan_clustering import build_canonical_plan_bundle


def _plan(table_name: str):
    return build_canonical_plan_bundle(
        [{
            "query_id": "q1",
            "query_sql_sha256": "a" * 64,
            "query_order": 1,
            "query_weight": 1.0,
            "dynamic": False,
            "binding_id": "static",
            "binding_values": None,
            "plan_mode": "custom",
            "raw_explain": {
                "query_block": {
                    "select_id": 1,
                    "table": {
                        "table_name": table_name,
                        "access_type": "ALL",
                        "key": None,
                        "rows_examined_per_scan": 1,
                    },
                },
            },
        }],
        registry_query_ids=["q1"],
        registry_sha256="b" * 64,
        registry_authority_verified=True,
    )


class _Replay:
    def __init__(self) -> None:
        self.ranks = []

    def __call__(self, candidate, _context):
        rank = int(candidate["rank"])
        self.ranks.append(rank)
        qps = 101.0 if rank == 8 else 99.0
        return {
            "qps": qps,
            "db_qps": qps,
            "db_qps_source": "database_status_counter",
            "p95_ms": 20.0,
            "p99_ms": 30.0,
            "correctness_passed": True,
            "error_rate": 0.0,
            "stable": True,
            "sample_coverage": 0.95,
            "oom": False,
            "mysql_restart": False,
            "container_restart": False,
            "utilization_pct": {"cpu": 0.91, "memory": 0.4, "io": 0.4},
            "three_level_fingerprint_evidence": {
                "execution_state_by_query": {
                    "q1": {
                        "workers": 0,
                        "hash_batches": 1,
                        "sort_mode": "none",
                        "spill_mode": "none",
                        "spill_bytes": 0,
                        "bitmap_lossy_pages": 0,
                        "intermediate_rows": 1,
                    },
                },
                "resource_vectors": {
                    "q1": {
                        "memory_peak_bytes": 1024,
                        "dynamic_execution_memory_bytes": 512,
                        "spill_bytes": 0,
                    },
                },
            },
            "evidence": {
                name: f"artifact://replay/{rank}/{name}"
                for name in REQUIRED_REAL_REPLAY_EVIDENCE
            },
        }

    def restore(self, _candidate, _context):
        return {
            "succeeded": True,
            "effective_params": {"p": 1.0},
            "fixed_settings_verified": True,
            "clean_template_verified": True,
            "evidence": "artifact://recovery",
        }


class PlanTreeReplayExtensionTest(unittest.TestCase):
    def test_schedule_fails_closed_without_incumbent_l1(self):
        selected, scan = _plan_tree_extension_schedule(
            [{"rank": 6, "subset_id": "s6", "strict_plan_cluster_key": "strict:" + "b" * 64}],
            incumbent_l1_sha256=None,
            already_replayed_ranks=range(1, 6),
            maximum_additional_replays=1,
        )
        self.assertEqual(selected, [])
        self.assertEqual(scan[0]["reason"], "incumbent_strict_l1_missing_fail_closed")

    def test_lowest_tier_extends_only_changed_l1_in_sealed_order_and_logs(self):
        incumbent_plan = _plan("incumbent_table")
        changed_plan = _plan("changed_table")
        incumbent_l1 = incumbent_plan["strict_cluster_id_sha256"]
        changed_l1 = changed_plan["strict_cluster_id_sha256"]
        candidates = []
        for rank in range(1, 10):
            bundle = changed_plan if rank >= 8 else incumbent_plan
            cluster_key = "strict:" + (changed_l1 if rank >= 8 else incumbent_l1)
            if rank == 7:
                cluster_key = "missing:7"
            candidates.append({
                "rank": rank,
                "subset_id": f"s{rank}",
                "optimized_params": {"p": float(rank)},
                "canonical_plan_bundle": bundle,
                "strict_plan_cluster_key": cluster_key,
                "final_score": float(100 - rank),
            })
        replay = _Replay()
        result = _orchestrate_staged_real_replays(
            candidates,
            callback=replay,
            baseline_params={"p": 1.0},
            baseline_validation={
                "qps": 100.0,
                "db_qps": 100.0,
                "db_qps_source": "database_status_counter",
                "p95_ms": 20.0,
                "p99_ms": 30.0,
                "utilization_pct": {"cpu": 0.9, "memory": 0.4, "io": 0.4},
                "canonical_plan_bundle": incumbent_plan,
            },
            sensitivity_table={"p": {"cpu": -0.1}},
            target_resource="cpu",
            benchmark="job",
            acceptance_tier="fallback_qps",
            plan_tree_extension_batch_size=1,
            plan_tree_extension_maximum_additional_replays=4,
        )

        self.assertEqual(replay.ranks, [1, 2, 3, 4, 5, 8])
        self.assertEqual(result["eligible_count"], 1)
        self.assertEqual(result["chosen"]["candidate_rank"], 8)
        audit = result["staged_replay_audit"]["plan_tree_replay_extension"]
        self.assertTrue(audit["triggered"])
        self.assertEqual(audit["additional_replay_count"], 1)
        self.assertEqual(audit["stop_reason"], "eligible_changed_plan_candidate_found")
        self.assertTrue(audit["predicted_order_reused_without_rerank"])
        self.assertFalse(audit["real_replay_results_used_for_rerank"])
        self.assertFalse(audit["qps_used_for_extension_selection"])
        reasons = {row["candidate_rank"]: row["reason"] for row in audit["candidate_scan"]}
        self.assertEqual(reasons[6], "same_plan_tree_as_incumbent")
        self.assertEqual(reasons[7], "candidate_strict_l1_missing_fail_closed")
        self.assertEqual(reasons[8], "appended_changed_plan_tree")
        self.assertEqual(reasons[9], "appended_changed_plan_tree")


if __name__ == "__main__":
    unittest.main()
