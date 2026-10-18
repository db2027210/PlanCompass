#!/usr/bin/env python3
"""Freeze the contextual soft resource-estimation contract requested 2026-08-09."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "mysql_algorithm_c_resource_model.py"
OUTPUT = (
    ROOT / "config/formal/frozen_mysql_algorithm_c_contextual_plan_physics_20260809_v1"
    / "model_contract.json"
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    if OUTPUT.exists():
        raise SystemExit(f"refusing to replace frozen output: {OUTPUT}")
    document = {
        "schema_version": "mysql_algorithm_c_contextual_plan_physics_contract_v1",
        "status": "FROZEN",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "authority": "user_clarification_2026-08-09",
        "model": {"path": str(MODEL.relative_to(ROOT)), "sha256": sha(MODEL)},
        "empirical_replay_basis": {
            "effective_replays": 151,
            "job_memory": {"samples": 16, "improved": 0, "neutral": 16, "worse": 0},
            "tpch_memory_historical_sf5": {
                "samples": 64, "improved": 0, "neutral": 59, "worse": 5,
            },
            "hybench_xp_cpu": {
                "samples": 35, "improved": 0, "neutral": 21, "worse": 14,
            },
            "hybench_xp_io": {
                "samples": 36, "improved": 23, "neutral": 0, "worse": 13,
            },
            "trusted_hard_reject_rules": 0,
            "operator_count_direction_is_not_deterministic": True,
        },
        "ranking": {
            "experience_prior_weight": 0.35,
            "contextual_plan_physics_weight": 0.65,
            "uncalibrated_transition_direction_log_step": 0.05,
            "confidence_adjusted_soft_score": True,
            "operator_count_hard_pruning": False,
            "real_replay_required": True,
        },
        "pre_replay_features": [
            "complete_plan_tree", "estimated_cardinality", "estimated_row_width",
            "estimated_nested_loop_multiplier", "operator_substitution_context",
            "sort_hash_temp_memory_and_spill", "sequential_and_random_pages",
            "frozen_cache_profile", "workload_concurrency",
        ],
        "unavailable_until_real_replay": [
            "actual_rows", "actual_loops", "actual_workers", "actual_spill",
            "runtime_cache_hits", "per_operator_execution_time",
        ],
        "hard_gates_unchanged": True,
        "algorithm_c_acceptance_tiers_unchanged": True,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=False)
    OUTPUT.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii"
    )
    print(OUTPUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
