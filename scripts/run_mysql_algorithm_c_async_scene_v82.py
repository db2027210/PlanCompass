#!/usr/bin/env python3
"""Run C v82 with serialized single-worker management resets."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import reset_mysql_xp_data_planes_mixed_four_worker_v3 as reset_v3
from scripts import run_mysql_algorithm_c_async_scene_v81 as predecessor


CONTRACT_ROOT = (
    ROOT / "config/formal/frozen_mysql_four_scene_algorithm_c_cpu_a_slots_20260828_v38"
)
PARENT_CONTRACT = CONTRACT_ROOT / "algorithm_c_async_contract.json"
OPTIMIZATION_CONTRACT = CONTRACT_ROOT / "outer_runtime_optimization.json"
CONTRACT = CONTRACT_ROOT / "global_monotonic_incumbent_contract.json"


def mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} object required")
    return dict(value)


def validate_contract() -> dict[str, Any]:
    alg = predecessor.predecessor.coverage_runtime.alg
    parent = alg.load(PARENT_CONTRACT)
    optimization = alg.load(OPTIMIZATION_CONTRACT)
    contract = alg.load(CONTRACT)
    reset_policy = mapping(
        contract.get("worker_reset_management_serialization"), "reset policy"
    )
    a_boundary = mapping(
        contract.get("algorithm_a_independent_check_boundary"), "A boundary"
    )
    if (
        parent.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or optimization.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("release_version") != "v82"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("parent_contract", {}).get("sha256") != alg.sha(PARENT_CONTRACT)
        or reset_policy.get("scope") != "single_worker_data_plane_replacement_only"
        or reset_policy.get("scene_wave_or_measurement_serialized") is not False
        or reset_policy.get("candidate_or_outer_policy_changed") is not False
        or a_boundary.get("algorithm_a_role") != "independent_posthoc_check_only"
        or a_boundary.get("algorithm_a_feedback_to_b_or_c") is not False
        or a_boundary.get(
            "algorithm_a_candidate_qps_range_or_ranking_prior_used_by_b_or_c"
        ) is not False
    ):
        raise RuntimeError("Algorithm C v82 reset/A-boundary contract differs")
    for document, label in ((parent, "parent"), (contract, "successor")):
        for name, raw in mapping(document.get("source_bindings"), f"{label} bindings").items():
            row = mapping(raw, f"{label} {name}")
            path = (ROOT / str(row.get("path") or "")).resolve()
            if not path.is_file() or path.is_symlink() or alg.sha(path) != row.get("sha256"):
                raise RuntimeError(f"Algorithm C v82 source binding differs: {name}")
    return {"parent": parent, "optimization": optimization, "successor": contract}


def main() -> int:
    validate_contract()
    v80 = predecessor.predecessor
    v79 = v80.predecessor
    v78 = v79.predecessor
    v77 = v78.predecessor
    v76 = v77.predecessor
    old_reset = v76.mixed_reset
    v76.mixed_reset = reset_v3
    try:
        return int(predecessor.main())
    finally:
        v76.mixed_reset = old_reset


if __name__ == "__main__":
    raise SystemExit(main())

