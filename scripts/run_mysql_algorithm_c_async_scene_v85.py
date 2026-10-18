#!/usr/bin/env python3
"""Run C v85 with collision-free run-scoped XP quarantine targets."""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import reset_mysql_xp_data_planes_mixed_four_worker_v4 as reset_v4
from scripts import run_mysql_algorithm_c_async_scene_v84 as predecessor


CONTRACT_ROOT = (
    ROOT / "config/formal/frozen_mysql_four_scene_algorithm_c_cpu_a_slots_20260828_v41"
)
PARENT_CONTRACT = CONTRACT_ROOT / "algorithm_c_async_contract.json"
OPTIMIZATION_CONTRACT = CONTRACT_ROOT / "outer_runtime_optimization.json"
CONTRACT = CONTRACT_ROOT / "global_monotonic_incumbent_contract.json"
CPU_PARTIAL_SOURCE = (
    ROOT / "history/mysql_four_scene_20260809"
    / "v1022_to_c_cpu_outer17_v84_reset_serial_resume_20260828_xp_cpu_xp_cpu"
)


def mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} object required")
    return dict(value)


def validate_contract() -> dict[str, Any]:
    v83 = predecessor.predecessor
    v82 = v83.predecessor
    v81 = v82.predecessor
    v80 = v81.predecessor
    alg = v80.coverage_runtime.alg
    parent = alg.load(PARENT_CONTRACT)
    optimization = alg.load(OPTIMIZATION_CONTRACT)
    contract = alg.load(CONTRACT)
    bindings = mapping(optimization.get("bindings"), "optimization bindings")
    boundary = mapping(
        contract.get("algorithm_a_independent_check_boundary"), "A boundary"
    )
    quarantine = mapping(
        contract.get("run_scoped_quarantine_successor"), "quarantine successor"
    )
    reset_path = Path(reset_v4.__file__).resolve()
    if (
        parent.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or optimization.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("release_version") != "v85"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("parent_contract", {}).get("sha256") != alg.sha(PARENT_CONTRACT)
        or bindings.get("reset_source_path") != str(reset_path.relative_to(ROOT))
        or bindings.get("reset_source_sha256") != alg.sha(reset_path)
        or quarantine.get("path_components")
        != ["run_id", "outer", "batch_kind", "reset_token", "worker_id"]
        or quarantine.get("predecessor_collision_scope") != "batch_kind/reset_token"
        or quarantine.get("candidate_or_outer_policy_changed") is not False
        or boundary.get("algorithm_a_role") != "independent_posthoc_check_only"
        or boundary.get("algorithm_a_feedback_to_b_or_c") is not False
        or boundary.get(
            "algorithm_a_candidate_qps_range_or_ranking_prior_used_by_b_or_c"
        ) is not False
    ):
        raise RuntimeError("Algorithm C v85 run-scoped quarantine contract differs")
    for document, label in ((parent, "parent"), (contract, "successor")):
        for name, raw in mapping(document.get("source_bindings"), f"{label} bindings").items():
            row = mapping(raw, f"{label} {name}")
            path = (ROOT / str(row.get("path") or "")).resolve()
            if not path.is_file() or path.is_symlink() or alg.sha(path) != row.get("sha256"):
                raise RuntimeError(f"Algorithm C v85 source binding differs: {name}")
    return {"parent": parent, "optimization": optimization, "successor": contract}


def main() -> int:
    validate_contract()
    v83 = predecessor.predecessor
    v82 = v83.predecessor
    v81 = v82.predecessor
    v80 = v81.predecessor
    old = {
        "v84_root": predecessor.CONTRACT_ROOT,
        "v84_parent": predecessor.PARENT_CONTRACT,
        "v84_optimization": predecessor.OPTIMIZATION_CONTRACT,
        "v84_contract": predecessor.CONTRACT,
        "v84_validate": predecessor.validate_contract,
        "v82_reset": v82.reset_v3,
    }
    predecessor.CONTRACT_ROOT = CONTRACT_ROOT
    predecessor.PARENT_CONTRACT = PARENT_CONTRACT
    predecessor.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    predecessor.CONTRACT = CONTRACT
    predecessor.validate_contract = validate_contract
    v82.reset_v3 = reset_v4
    try:
        return int(predecessor.main())
    finally:
        predecessor.CONTRACT_ROOT = old["v84_root"]
        predecessor.PARENT_CONTRACT = old["v84_parent"]
        predecessor.OPTIMIZATION_CONTRACT = old["v84_optimization"]
        predecessor.CONTRACT = old["v84_contract"]
        predecessor.validate_contract = old["v84_validate"]
        v82.reset_v3 = old["v82_reset"]


if __name__ == "__main__":
    raise SystemExit(main())
