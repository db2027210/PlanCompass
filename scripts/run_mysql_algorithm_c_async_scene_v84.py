#!/usr/bin/env python3
"""Run C v84 with every v80 runtime contract alias rebound to v40."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_c_async_scene_v83 as predecessor


CONTRACT_ROOT = (
    ROOT / "config/formal/frozen_mysql_four_scene_algorithm_c_cpu_a_slots_20260828_v40"
)
PARENT_CONTRACT = CONTRACT_ROOT / "algorithm_c_async_contract.json"
OPTIMIZATION_CONTRACT = CONTRACT_ROOT / "outer_runtime_optimization.json"
CONTRACT = CONTRACT_ROOT / "global_monotonic_incumbent_contract.json"


def mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} object required")
    return dict(value)


def validate_contract() -> dict[str, Any]:
    v82 = predecessor.predecessor
    v81 = v82.predecessor
    v80 = v81.predecessor
    alg = v80.coverage_runtime.alg
    parent = alg.load(PARENT_CONTRACT)
    optimization = alg.load(OPTIMIZATION_CONTRACT)
    contract = alg.load(CONTRACT)
    bindings = mapping(optimization.get("bindings"), "optimization bindings")
    alias = mapping(contract.get("runtime_contract_alias_binding"), "runtime alias")
    boundary = mapping(
        contract.get("algorithm_a_independent_check_boundary"), "A boundary"
    )
    reset_path = ROOT / "scripts/reset_mysql_xp_data_planes_mixed_four_worker_v3.py"
    if (
        parent.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or optimization.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("release_version") != "v84"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("parent_contract", {}).get("sha256") != alg.sha(PARENT_CONTRACT)
        or bindings.get("reset_source_path") != str(reset_path.relative_to(ROOT))
        or bindings.get("reset_source_sha256") != alg.sha(reset_path)
        or alias.get("v80_contract_globals_rebound") is not True
        or alias.get("coverage_runtime_optimization_rebound") is not True
        or boundary.get("algorithm_a_role") != "independent_posthoc_check_only"
        or boundary.get("algorithm_a_feedback_to_b_or_c") is not False
        or boundary.get(
            "algorithm_a_candidate_qps_range_or_ranking_prior_used_by_b_or_c"
        ) is not False
    ):
        raise RuntimeError("Algorithm C v84 complete runtime alias contract differs")
    for document, label in ((parent, "parent"), (contract, "successor")):
        for name, raw in mapping(document.get("source_bindings"), f"{label} bindings").items():
            row = mapping(raw, f"{label} {name}")
            path = (ROOT / str(row.get("path") or "")).resolve()
            if not path.is_file() or path.is_symlink() or alg.sha(path) != row.get("sha256"):
                raise RuntimeError(f"Algorithm C v84 source binding differs: {name}")
    return {"parent": parent, "optimization": optimization, "successor": contract}


def main() -> int:
    validate_contract()
    v82 = predecessor.predecessor
    v81 = v82.predecessor
    v80 = v81.predecessor
    old = {
        "v83_root": predecessor.CONTRACT_ROOT,
        "v83_parent": predecessor.PARENT_CONTRACT,
        "v83_optimization": predecessor.OPTIMIZATION_CONTRACT,
        "v83_contract": predecessor.CONTRACT,
        "v83_validate": predecessor.validate_contract,
        "v80_root": v80.CONTRACT_ROOT,
        "v80_parent": v80.PARENT_CONTRACT,
        "v80_optimization": v80.OPTIMIZATION_CONTRACT,
        "v80_contract": v80.CONTRACT,
        "v80_validate": v80.validate_contract,
    }
    predecessor.CONTRACT_ROOT = CONTRACT_ROOT
    predecessor.PARENT_CONTRACT = PARENT_CONTRACT
    predecessor.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    predecessor.CONTRACT = CONTRACT
    predecessor.validate_contract = validate_contract
    v80.CONTRACT_ROOT = CONTRACT_ROOT
    v80.PARENT_CONTRACT = PARENT_CONTRACT
    v80.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    v80.CONTRACT = CONTRACT
    v80.validate_contract = validate_contract
    try:
        return int(predecessor.main())
    finally:
        predecessor.CONTRACT_ROOT = old["v83_root"]
        predecessor.PARENT_CONTRACT = old["v83_parent"]
        predecessor.OPTIMIZATION_CONTRACT = old["v83_optimization"]
        predecessor.CONTRACT = old["v83_contract"]
        predecessor.validate_contract = old["v83_validate"]
        v80.CONTRACT_ROOT = old["v80_root"]
        v80.PARENT_CONTRACT = old["v80_parent"]
        v80.OPTIMIZATION_CONTRACT = old["v80_optimization"]
        v80.CONTRACT = old["v80_contract"]
        v80.validate_contract = old["v80_validate"]


if __name__ == "__main__":
    raise SystemExit(main())

