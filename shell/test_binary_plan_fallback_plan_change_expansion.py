#!/usr/bin/env python3
"""Regression tests for fallback-tier plan-change replay expansion."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any, Dict, Tuple


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from binary_plan_resource_tuner import (  # noqa: E402
    candidate_plan_probe_task,
    plan_change_replay_expansion_state,
    plan_probe_coverage_audit,
    plan_signature_set_fingerprint,
    select_plan_change_replay_expansion_candidates,
    select_distinct_base_topk_candidates,
    select_unique_changed_plan_candidates,
)


CFG = {
    "binary_plan_tuning": {
        "binary_search": {
            "nested_loop": {
                "outer_acceptance": {
                    "acceptance_tier_downgrade_after_rounds": 2,
                },
                "fallback_plan_change_replay_expansion": {
                    "enabled": True,
                    "trigger_after_fallback_miss_rounds": 1,
                    "max_extra_candidates_per_round": 5,
                },
            }
        }
    }
}


def candidate(
    raw_index: int,
    *,
    status: str = "ok",
    plan_changed: bool = True,
    fingerprint: str = "",
) -> Tuple[int, Dict[str, Any], Dict[str, Any]]:
    return (
        raw_index,
        {
            "raw_candidate_index": raw_index,
            "mask": f"mask-{raw_index}",
            "params": {"seq_page_cost": float(raw_index)},
        },
        {
            "status": status,
            "plan": {"changed": plan_changed, "changed_count": 1 if plan_changed else 0},
            "candidate_plan_fingerprint": fingerprint,
        },
    )


class FallbackPlanChangeReplayExpansionTest(unittest.TestCase):
    def test_candidate_probe_with_partial_coverage_cannot_enter_replay(self) -> None:
        cfg = {
            "binary_plan_tuning": {
                "workload": {
                    "plan_coverage_gate": {
                        "enabled": True,
                        "catalog_complete": True,
                        "expected_query_count": 2,
                        "block_physical_base_topk": True,
                    }
                }
            }
        }
        baseline_probe = {
            "plan_signatures": {"file_1": "a", "file_2": "b"},
            "feature_inputs": {"cpu": {}, "memory": {}, "io": {}},
            "resource_avg": {},
        }
        partial_candidate_probe = {
            "plan_signatures": {"file_1": "c", "file_2": "ERROR:x"},
            "feature_inputs": {"cpu": {}, "memory": {}, "io": {}},
            "resource_avg": {},
            "resource_error_profile": {},
            "query_count": 2,
            "explained_query_count": 1,
            "errors": [{"queryid": "file_2", "error": "boom"}],
        }

        class FakeProbe:
            def __init__(self, _cfg: Dict[str, Any]) -> None:
                pass

            def probe(
                self,
                _queries: Any,
                _params: Dict[str, float],
            ) -> Dict[str, Any]:
                return partial_candidate_probe

        from unittest.mock import patch

        with patch("binary_plan_resource_tuner.PlanFeatureProbe", FakeProbe):
            changes = candidate_plan_probe_task(
                cfg,
                [],
                baseline_probe,
                {"params": {}, "target": "cpu"},
            )

        self.assertEqual("failed", changes["status"])
        self.assertEqual(
            "candidate_plan_probe_coverage_incomplete",
            changes["reason"],
        )

    def test_partial_plan_catalog_does_not_dedupe_base_but_cannot_expand(self) -> None:
        row = candidate(1, fingerprint="partial-new-plan")
        row[1]["signature"] = "distinct-parameter-vector"
        row[2]["candidate_plan_coverage_gate"] = {
            "enabled": True,
            "passed": False,
            "block_physical_base_topk": False,
        }

        base_selected, _base_rejected, _base_audit = select_distinct_base_topk_candidates(
            [row]
        )
        expanded, expansion_audit = select_plan_change_replay_expansion_candidates(
            [row],
            max_extra_candidates=1,
        )

        self.assertEqual([1], [item[0] for item in base_selected])
        self.assertEqual([], expanded)
        self.assertEqual(
            1,
            expansion_audit["exclusion_counts"]["plan_probe_workload_coverage_incomplete"],
        )

    def test_plan_dedupe_coverage_gate_requires_complete_exact_catalog(self) -> None:
        cfg = {
            "binary_plan_tuning": {
                "workload": {
                    "plan_coverage_gate": {
                        "enabled": True,
                        "catalog_complete": True,
                        "coverage_scope": "test_q1_q2",
                        "expected_query_count": 2,
                        "expected_query_ids": ["file_1", "file_2"],
                    }
                }
            }
        }
        complete = plan_probe_coverage_audit(
            cfg,
            {
                "query_count": 2,
                "explained_query_count": 2,
                "plan_signatures": {"file_1": "a", "file_2": "b"},
                "errors": [],
            },
        )
        incomplete = plan_probe_coverage_audit(
            cfg,
            {
                "query_count": 2,
                "explained_query_count": 1,
                "plan_signatures": {"file_1": "a", "file_2": "ERROR:x"},
                "errors": [{"queryid": "file_2", "error": "boom"}],
            },
        )

        self.assertTrue(complete["passed"])
        self.assertFalse(incomplete["passed"])
        self.assertFalse(incomplete["checks"]["all_queries_explained"])
        self.assertFalse(incomplete["checks"]["no_probe_errors"])

    def test_plan_dedupe_coverage_gate_rejects_unproven_catalog(self) -> None:
        audit = plan_probe_coverage_audit(
            {
                "binary_plan_tuning": {
                    "workload": {
                        "plan_coverage_gate": {
                            "enabled": True,
                            "catalog_complete": False,
                            "expected_query_count": 2,
                        }
                    }
                }
            },
            {
                "query_count": 2,
                "explained_query_count": 2,
                "plan_signatures": {"file_1": "a", "file_2": "b"},
                "errors": [],
            },
        )

        self.assertFalse(audit["passed"])
        self.assertFalse(audit["checks"]["catalog_declared_complete"])

    def test_arms_only_after_one_completed_fallback_tier_topk_miss(self) -> None:
        first_fallback_attempt = plan_change_replay_expansion_state(CFG, 4)
        after_first_fallback_miss = plan_change_replay_expansion_state(CFG, 5)

        self.assertEqual("fallback", first_fallback_attempt["required_acceptance_tier"])
        self.assertFalse(first_fallback_attempt["active"])
        self.assertEqual(0, first_fallback_attempt["completed_fallback_miss_rounds"])
        self.assertTrue(after_first_fallback_miss["active"])
        self.assertEqual(1, after_first_fallback_miss["completed_fallback_miss_rounds"])
        self.assertEqual(5, after_first_fallback_miss["max_extra_candidates_per_round"])

    def test_extra_replay_requires_successful_changed_unique_plan_tree(self) -> None:
        ranked = [
            candidate(1, fingerprint="already-replayed"),
            candidate(2, plan_changed=False, fingerprint="unchanged"),
            candidate(3, status="failed", fingerprint="failed-probe"),
            candidate(4, fingerprint=""),
            candidate(5, fingerprint="new-plan-a"),
            candidate(6, fingerprint="new-plan-a"),
            candidate(7, fingerprint="new-plan-b"),
            candidate(8, fingerprint="new-plan-c"),
        ]

        selected, audit = select_plan_change_replay_expansion_candidates(
            ranked,
            max_extra_candidates=2,
            excluded_plan_fingerprints={"already-replayed"},
        )

        self.assertEqual([5, 7], [row[0] for row in selected])
        self.assertEqual(2, audit["selected_extra_count"])
        self.assertEqual(6, audit["eligible_plan_changed_count"])
        self.assertEqual(3, audit["unique_eligible_count"])
        self.assertEqual(2, audit["exclusion_counts"]["duplicate_plan_fingerprint"])
        self.assertEqual(1, audit["exclusion_counts"]["plan_tree_unchanged"])
        self.assertEqual(1, audit["exclusion_counts"]["plan_probe_not_ok"])
        self.assertEqual(1, audit["exclusion_counts"]["missing_candidate_plan_fingerprint"])
        self.assertEqual(1, audit["exclusion_counts"]["extra_replay_cap_reached"])

    def test_workload_plan_fingerprint_is_stable_across_mapping_order(self) -> None:
        left = {"q2": "plan-b", "q1": "plan-a"}
        right = {"q1": "plan-a", "q2": "plan-b"}

        self.assertEqual(
            plan_signature_set_fingerprint(left),
            plan_signature_set_fingerprint(right),
        )
        self.assertNotEqual(
            plan_signature_set_fingerprint(left),
            plan_signature_set_fingerprint({"q1": "plan-a", "q2": "plan-c"}),
        )

    def test_base_topk_keeps_same_plan_for_distinct_parameter_vectors(self) -> None:
        ranked = [
            candidate(1, fingerprint="baseline"),
            candidate(2, fingerprint="new-plan-a"),
            candidate(3, fingerprint="new-plan-a"),
            candidate(4, fingerprint="already-replayed"),
            candidate(5, plan_changed=False, fingerprint="unchanged"),
            candidate(6, fingerprint="new-plan-b"),
        ]
        for raw_index, row, _changes in ranked:
            row["signature"] = f"params-{raw_index}"

        selected, rejected, audit = select_distinct_base_topk_candidates(
            ranked,
            excluded_parameter_signatures={"params-4"},
        )

        self.assertEqual([1, 2, 3, 5, 6], [row[0] for row in selected])
        self.assertEqual(1, len(rejected))
        self.assertEqual(5, audit["eligible_distinct_parameter_count"])
        self.assertEqual(
            1,
            audit["rejection_reason_counts"]["parameter_vector_already_replayed_in_interval"],
        )
        self.assertTrue(audit["same_plan_different_parameters_retained"])
        self.assertEqual(1, audit["retained_duplicate_plan_fingerprint_count"])


if __name__ == "__main__":
    unittest.main()
