#!/usr/bin/env python3
"""End-to-end regression test for the nested binary-search control flow."""

from __future__ import annotations

import argparse
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import binary_plan_resource_tuner as tuner  # noqa: E402


class FakePlanFeatureProbe:
    def __init__(self, _cfg: Dict[str, Any]) -> None:
        self.explain_workers = 1

    def probe(self, _queries: Any, _params: Dict[str, float]) -> Dict[str, Any]:
        return {
            "resource_avg": {"cpu": 100.0, "memory": 100.0, "io": 100.0},
            "resource_error_profile": {},
            "query_count": 1,
        }


class NestedBinarySearchIntegrationTest(unittest.TestCase):
    @staticmethod
    def config() -> Dict[str, Any]:
        initial = {
            "seq_page_cost": 1.0,
            "random_page_cost": 4.0,
            "cpu_tuple_cost": 0.01,
            "cpu_index_tuple_cost": 0.005,
            "cpu_operator_cost": 0.0025,
            "parallel_setup_cost": 1.0,
            "parallel_tuple_cost": 0.1,
        }
        ranges = {
            name: {"min_value": value, "max_value": value}
            for name, value in initial.items()
        }
        ranges["parallel_setup_cost"] = {"min_value": 0.0, "max_value": 2.0}
        ranges["seq_page_cost"] = {"min_value": 0.0, "max_value": 2.0}
        return {
            "initial_params": initial,
            "param_ranges": ranges,
            "binary_plan_tuning": {
                "workload_name": "hybenchxp",
                "acceptance_policy": {
                    "min_target_drop_pct": 5.0,
                    "max_target_drop_pct": 10.0,
                    "min_db_qps_gain_pct": 15.0,
                    "require_other_resource_up": False,
                    "resource_capacity": {
                        "cpu_cores": 200.0,
                        "memory_gib": 200.0,
                        "io_mib_s": 200.0,
                    },
                },
                "binary_search": {
                    "max_rounds": 3,
                    "max_candidates_per_round": 2,
                    "plan_probe_workers": 1,
                    "stop_only_on_epsilon": True,
                    "zero_accepted_backtrack": False,
                    "experiment_strategy": "NESTED",
                    "range_anchor_policy": tuner.NESTED_RESOURCE_SEMANTIC_POLICY,
                    "epsilon": {
                        "default": 1.0,
                        "parallel_setup_cost": 1.0,
                        "seq_page_cost": 1.0,
                    },
                    "real_score_b": {
                        "resource_up_scale": 0.10,
                        "bonus_coefficient": 0.02,
                    },
                    "real_score_c": {
                        "resource_up_scale": 0.10,
                        "bonus_coefficient": 0.02,
                    },
                    "nested_loop": {
                        "inner": {
                            "estimated_recall_topk": 2,
                            "qps_considered": False,
                            "handoff_cardinality": "exactly_one_best_candidate_per_inner_attempt",
                        },
                        "outer_acceptance": {
                            "min_db_qps_gain_pct": 0.0,
                            "min_target_drop_pct": 0.0,
                            "shrink_only_when_accepted": True,
                        },
                        "final_validation": {"min_db_qps_gain_pct": 15.0},
                    },
                    "replay_candidate_pruning": {"enabled": False},
                },
                "validation": {
                    "parallel_baseline": {"enabled": False},
                    "docker_workers": {"enabled": False},
                    "vm_replay": {"enabled": False},
                },
            },
        }

    @staticmethod
    def validation(
        label: str,
        params: Dict[str, float],
        *,
        qps: float,
        cpu: float,
        memory: float,
        io: float = 100.0,
        final: bool = False,
    ) -> Dict[str, Any]:
        row: Dict[str, Any] = {
            "label": label,
            "status": "ok",
            "params": copy.deepcopy(params),
            "effective_params": copy.deepcopy(params),
            "db_qps": qps,
            "db_xact_tps": qps / 2.0,
            "resources": {"cpu": cpu, "memory": memory, "io": io},
            "resource_stats": {"resource_monitor_ok": True},
            "resource_samples": {"cpu_cores": 10, "memory_bytes": 10, "io_bps": 10},
            "p90_ms": 8.0,
            "p95_ms": 10.0,
            "p99_ms": 20.0,
        }
        if final:
            row.update(
                {
                    "actual_utilization_pct": {"cpu": 75.0, "memory": 70.0, "io": 50.0},
                    "runtime_capability": {"cpu": 8.0, "memory": 8.0, "io": 200.0},
                    "runtime_scoring": {
                        "overload_threshold": 0.8,
                        "resource_target_ranges": {
                            "cpu": {"min_pct": 65.0, "max_pct": 80.0, "target_pct": 72.5}
                        },
                    },
                }
            )
        return row

    def test_reject_keeps_interval_next_attempt_accepts_and_final_replays(self) -> None:
        cfg = self.config()
        calls: list[str] = []

        def fake_validation(
            _cfg: Dict[str, Any],
            params: Dict[str, float],
            *,
            output_dir: Path,
            label: str,
            worker: Any = None,
        ) -> Dict[str, Any]:
            _ = output_dir, worker
            calls.append(label)
            if label == "cpu_baseline":
                return self.validation(
                    label, params, qps=100.0, cpu=100.0, memory=100.0
                )
            if label.endswith("_LL"):
                return self.validation(label, params, qps=90.0, cpu=93.0, memory=110.0)
            if label.endswith("_LR"):
                return self.validation(label, params, qps=80.0, cpu=96.0, memory=100.0)
            if label.endswith("_RL"):
                # Strict tier: QPS rises, CPU bottleneck falls, and a
                # non-bottleneck resource rises.
                return self.validation(label, params, qps=110.0, cpu=94.0, memory=105.0)
            if label.endswith("_RR"):
                return self.validation(label, params, qps=105.0, cpu=97.0, memory=100.0)
            if label == "cpu_nested_final_confirmation":
                return self.validation(
                    label,
                    params,
                    qps=120.0,
                    cpu=93.0,
                    memory=105.0,
                    final=True,
                )
            raise AssertionError(f"unexpected validation label: {label}")

        def fake_probe_task(
            _cfg: Dict[str, Any],
            _queries: Any,
            _baseline_probe: Dict[str, Any],
            candidate: Dict[str, Any],
        ) -> Dict[str, Any]:
            rank = {"LL": 4.0, "LR": 3.0, "RL": 2.0, "RR": 1.0}[candidate["mask"]]
            return {
                "status": "ok",
                "any_changed": True,
                "plan": {"changed": True, "changed_count": 1},
                "candidate_plan_fingerprint": f"plan-{candidate['mask']}",
                "rank": rank,
                "candidate_resource_avg": {"cpu": 100.0 - rank},
                "baseline_resource_avg": {"cpu": 100.0},
                "candidate_resource_error_profile": {},
            }

        with tempfile.TemporaryDirectory() as temp_dir_text:
            out_dir = Path(temp_dir_text)
            args = argparse.Namespace(
                config="unused.json",
                target="cpu",
                output_dir=str(out_dir),
                active_params="parallel_setup_cost,seq_page_cost",
                max_rounds=3,
                max_candidates=2,
                resume=False,
                baseline_only=False,
            )
            with (
                mock.patch.object(tuner, "load_config", return_value=cfg),
                mock.patch.object(tuner, "exclusive_vm_disk_lock_error", return_value=None),
                mock.patch.object(tuner, "PlanFeatureProbe", FakePlanFeatureProbe),
                mock.patch.object(
                    tuner,
                    "workload_queries_with_sampling",
                    return_value=([{"sql": "select 1"}], {"mode": "test"}),
                ),
                mock.patch.object(tuner, "candidate_plan_probe_task", side_effect=fake_probe_task),
                mock.patch.object(
                    tuner,
                    "probe_topk_rank_key",
                    side_effect=lambda changes, *_args, **_kwargs: (changes.get("rank", 0.0),),
                ),
                mock.patch.object(
                    tuner,
                    "replay_pruning_status_from_bounds",
                    return_value={"pruned": False, "reason": "test_disabled"},
                ),
                mock.patch.object(tuner, "run_real_validation_safe", side_effect=fake_validation),
            ):
                return_code = tuner.binary_search(args)

            self.assertEqual(0, return_code)
            rows = [
                json.loads(line)
                for line in (out_dir / "cpu_binary_search_hybenchxp.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
                if line.strip()
            ]
            summaries = [row for row in rows if row.get("event") == "round_summary"]
            decisions = [row for row in rows if row.get("event") == "outer_candidate_decision"]
            confirmations = [
                row for row in rows if row.get("event") == "post_convergence_final_validation"
            ]

            self.assertEqual(2, len(summaries))
            self.assertEqual(2, len([row for row in decisions if int(row["round"]) == 1]))
            self.assertFalse(decisions[0]["accepted"])
            self.assertEqual(summaries[0]["previous_ranges"], summaries[0]["updated_ranges"])
            self.assertEqual("keep_interval_and_continue_inner_loop", summaries[0]["interval_action"])
            accepted_decisions = [row for row in decisions if row.get("accepted")]
            self.assertEqual(1, len(accepted_decisions))
            self.assertEqual("cpu_r02_c003_RL", accepted_decisions[0]["candidate_label"])
            self.assertEqual("cpu_r02_c003_RL", summaries[1]["range_anchor_label"])
            self.assertNotEqual(summaries[1]["previous_ranges"], summaries[1]["updated_ranges"])
            self.assertEqual(1, len(confirmations))
            self.assertTrue(confirmations[0]["final_goal"]["goal_reached"])
            self.assertEqual("cpu_nested_final_confirmation", calls[-1])

            final = json.loads((out_dir / "final_for_cpu.json").read_text(encoding="utf-8"))
            self.assertTrue(final["converged"])
            self.assertEqual("cpu_r02_c003_RL", final["strategy_final_point"]["label"])
            self.assertTrue(final["final_goal"]["goal_reached"])

    def test_fallback_top5_miss_expands_next_round_with_only_new_plan_trees(self) -> None:
        cfg = self.config()
        initial = cfg["initial_params"]
        for name, value in initial.items():
            cfg["param_ranges"][name] = {
                "min_value": max(0.0, value * 0.5),
                "max_value": max(0.0001, value * 1.5),
            }
        search = cfg["binary_plan_tuning"]["binary_search"]
        search.update(
            {
                "max_rounds": 12,
                "max_candidates_per_round": 5,
                "stop_only_on_epsilon": False,
            }
        )
        search["nested_loop"]["outer_acceptance"][
            "acceptance_tier_downgrade_after_rounds"
        ] = 5
        search["nested_loop"]["fallback_plan_change_replay_expansion"] = {
            "enabled": True,
            "trigger_after_fallback_miss_rounds": 1,
            "max_extra_candidates_per_round": 5,
        }
        candidate_labels: list[str] = []

        def fake_validation(
            _cfg: Dict[str, Any],
            params: Dict[str, float],
            *,
            output_dir: Path,
            label: str,
            worker: Any = None,
        ) -> Dict[str, Any]:
            _ = output_dir, worker
            if label == "cpu_baseline":
                return self.validation(
                    label, params, qps=100.0, cpu=100.0, memory=100.0
                )
            candidate_labels.append(label)
            return self.validation(
                label, params, qps=90.0, cpu=93.0, memory=110.0
            )

        def fake_probe_task(
            _cfg: Dict[str, Any],
            _queries: Any,
            _baseline_probe: Dict[str, Any],
            candidate_row: Dict[str, Any],
        ) -> Dict[str, Any]:
            raw_index = int(candidate_row["raw_candidate_index"])
            return {
                "status": "ok",
                "any_changed": True,
                "plan": {"changed": True, "changed_count": 1},
                "candidate_plan_fingerprint": f"plan-{candidate_row['mask']}",
                "rank": -float(raw_index),
                "candidate_resource_avg": {"cpu": 90.0},
                "baseline_resource_avg": {"cpu": 100.0},
                "candidate_resource_error_profile": {},
            }

        with tempfile.TemporaryDirectory() as temp_dir_text:
            out_dir = Path(temp_dir_text)
            args = argparse.Namespace(
                config="unused.json",
                target="cpu",
                output_dir=str(out_dir),
                active_params=",".join(initial),
                max_rounds=12,
                max_candidates=5,
                resume=False,
                baseline_only=False,
            )
            with (
                mock.patch.object(tuner, "load_config", return_value=cfg),
                mock.patch.object(tuner, "exclusive_vm_disk_lock_error", return_value=None),
                mock.patch.object(tuner, "PlanFeatureProbe", FakePlanFeatureProbe),
                mock.patch.object(
                    tuner,
                    "workload_queries_with_sampling",
                    return_value=([{"sql": "select 1"}], {"mode": "test"}),
                ),
                mock.patch.object(tuner, "candidate_plan_probe_task", side_effect=fake_probe_task),
                mock.patch.object(
                    tuner,
                    "probe_topk_rank_key",
                    side_effect=lambda changes, *_args, **_kwargs: (changes.get("rank", 0.0),),
                ),
                mock.patch.object(
                    tuner,
                    "replay_pruning_status_from_bounds",
                    return_value={"pruned": False, "reason": "test_disabled"},
                ),
                mock.patch.object(tuner, "run_real_validation_safe", side_effect=fake_validation),
            ):
                return_code = tuner.binary_search(args)

            self.assertEqual(0, return_code)
            rows = [
                json.loads(line)
                for line in (out_dir / "cpu_binary_search_hybenchxp.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
                if line.strip()
            ]
            summaries = [row for row in rows if row.get("event") == "round_summary"]
            expansion_events = [
                row
                for row in rows
                if row.get("event") == "fallback_plan_change_replay_expansion"
            ]
            armed_events = [
                row
                for row in rows
                if row.get("event") == "fallback_plan_change_replay_expansion_armed"
            ]
            expanded_validations = [
                row
                for row in rows
                if row.get("event") == "candidate_validation"
                and row.get("fallback_plan_change_replay_expansion") is True
            ]

            self.assertEqual(12, len(summaries))
            self.assertEqual(5, summaries[10]["real_validation_count"])
            self.assertTrue(
                summaries[10]["fallback_plan_change_replay_expansion_next_round_armed"]
            )
            self.assertEqual(10, summaries[11]["real_validation_count"])
            self.assertEqual(5, len(expanded_validations))
            self.assertTrue(all(row["plan_tree_changed"] for row in expanded_validations))
            self.assertEqual(5, expansion_events[-1]["selected_extra_count"])
            self.assertEqual(10, expansion_events[-1]["effective_replay_cap"])
            self.assertEqual(
                "arm_after_fallback_tier_base_topk_miss",
                armed_events[0]["action"],
            )
            self.assertEqual(65, len(candidate_labels))


if __name__ == "__main__":
    unittest.main()
