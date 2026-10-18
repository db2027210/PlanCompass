#!/usr/bin/env python3
"""Resume Algorithm C after repeated XP incumbent technical invalidation."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import mysql_algorithm_c_plan_replay_pruning_v3 as pruning_v3
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v20 as v20


runtime = v20.v19.v18.v17.v16.v15.v13.v11.v10.v4.runtime
alg = v20.alg
ORIGINAL_STATE_DOCUMENT = v20.state_document
PLAN_PRUNING_CONTRACT = (
    ROOT / "config/formal/mysql_algorithm_c_plan_replay_pruning_20260817_v3"
    / "contract.json"
)


def state_document(**kwargs: Any) -> dict[str, Any]:
    document = ORIGINAL_STATE_DOCUMENT(**kwargs)
    document["technical_plan_instability_pruning_release"] = {
        "release_version": "v21",
        "source_path": str(Path(__file__).resolve()),
        "source_sha256": alg.sha(Path(__file__).resolve()),
        "contract": pruning_v3.CONTRACT_VERSION,
        "contract_path": str(PLAN_PRUNING_CONTRACT),
        "contract_sha256": alg.sha(PLAN_PRUNING_CONTRACT),
        "minimum_independent_formal_incumbent_incidents": 2,
        "accepted_counterexample_overridden_only_for_technical_safety": True,
        "qps_pruning_rules_changed": False,
        "candidate_score_resource_model_gradient_and_top5_changed": False,
        "workload_docker_concurrency_and_execution_order_changed": False,
    }
    return document


def main() -> int:
    runtime.rank_and_prune_candidates = pruning_v3.rank_and_prune_candidates
    runtime.PLAN_PRUNING_CONTRACT_VERSION = pruning_v3.CONTRACT_VERSION
    runtime.PLAN_PRUNING_CONTRACT = PLAN_PRUNING_CONTRACT
    v20.state_document = state_document
    return v20.main()


if __name__ == "__main__":
    raise SystemExit(main())
