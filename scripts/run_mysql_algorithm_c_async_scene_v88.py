#!/usr/bin/env python3
"""Run CPU C v88 with a current-checkpoint four-worker incumbent baseline."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_c_async_scene_v87 as predecessor


CONTRACT_ROOT = (
    ROOT / "config/formal/frozen_mysql_four_scene_algorithm_c_cpu_a_slots_20260828_v44"
)
PARENT_CONTRACT = CONTRACT_ROOT / "algorithm_c_async_contract.json"
OPTIMIZATION_CONTRACT = CONTRACT_ROOT / "outer_runtime_optimization.json"
CONTRACT = CONTRACT_ROOT / "global_monotonic_incumbent_contract.json"
BASELINE = (
    ROOT / "history/mysql_four_scene_20260809"
    / "v1032_c_cpu_outer17_checkpoint_incumbent_baseline_20260828"
    / "checkpoint_incumbent_baseline.json"
)
SCENE = "hybench_xp_cpu"


def mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} object required")
    return dict(value)


def validate_contract() -> dict[str, Any]:
    alg = predecessor.partial_runtime.alg
    parent = alg.load(PARENT_CONTRACT)
    optimization = alg.load(OPTIMIZATION_CONTRACT)
    contract = alg.load(CONTRACT)
    carry = mapping(contract.get("current_checkpoint_incumbent_baseline"), "baseline carry")
    a_boundary = mapping(contract.get("algorithm_a_independent_check_boundary"), "A boundary")
    if (
        parent.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or optimization.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("release_version") != "v88"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("parent_contract", {}).get("sha256") != alg.sha(PARENT_CONTRACT)
        or carry.get("scene") != SCENE
        or carry.get("worker_count") != 4
        or carry.get("physical_incumbent_remeasurement") is not False
        or carry.get("accepted_or_rejected_credit") != 0
        or a_boundary.get("algorithm_a_role") != "independent_posthoc_check_only"
        or a_boundary.get("algorithm_a_feedback_to_b_or_c") is not False
    ):
        raise RuntimeError("Algorithm C v88 checkpoint baseline contract differs")
    for document, label in ((parent, "parent"), (contract, "successor")):
        for name, raw in mapping(document.get("source_bindings"), f"{label} bindings").items():
            row = mapping(raw, f"{label} {name}")
            path = (ROOT / str(row.get("path") or "")).resolve()
            if not path.is_file() or path.is_symlink() or alg.sha(path) != row.get("sha256"):
                raise RuntimeError(f"Algorithm C v88 source binding differs: {name}")
    return {"parent": parent, "optimization": optimization, "successor": contract}


def validate_current_baseline(
    *, reconstructed: Mapping[str, Any],
) -> tuple[Path, dict[str, Any]]:
    alg = predecessor.partial_runtime.alg
    document = alg.load(BASELINE)
    rows = mapping(document.get("measurements"), "baseline measurements")
    incumbent = mapping(mapping(reconstructed.get("incumbents"), "incumbents").get(SCENE), "incumbent")
    params = mapping(mapping(reconstructed.get("params_by_scene"), "params").get(SCENE), "params")
    if (
        document.get("schema_version") != "mysql_algorithm_c_checkpoint_incumbent_baseline_v2"
        or document.get("status") != "PASS"
        or document.get("scene") != SCENE
        or document.get("checkpoint_incumbent_carry_forward") is not True
        or document.get("physical_workload_started") is not False
        or set(rows) != {"xp_cpu_w1", "xp_cpu_w2", "xp_cpu_w3", "xp_cpu_w4"}
        or any(
            row.get("scene") != SCENE
            or row.get("hard_gate_passed") is not True
            or row.get("coverage_complete") is not True
            or row.get("checkpoint_incumbent_carry_forward") is not True
            or row.get("formal_measurement_performed") is not False
            or float(row.get("db_qps") or 0) != float(incumbent["db_qps"])
            or alg.optimizer_params_key(row.get("params") or {})
            != alg.optimizer_params_key(params)
            for row in rows.values()
        )
    ):
        raise RuntimeError("Algorithm C v88 current checkpoint baseline differs")
    return BASELINE, document


_ORIGINAL_PARTIAL = predecessor.validate_post_fallback_partial


def validate_post_fallback_partial(**kwargs: Any) -> dict[str, Any] | None:
    result = _ORIGINAL_PARTIAL(**kwargs)
    if result is None:
        return None
    baseline_path, _ = validate_current_baseline(reconstructed=kwargs["reconstructed"])
    result["baseline"] = {
        "path": str(baseline_path),
        "sha256": predecessor.partial_runtime.alg.sha(baseline_path),
    }
    result["checkpoint_incumbent_baseline_carry_forward"] = True
    result["checkpoint_incumbent_baseline_worker_count"] = 4
    return result


def main() -> int:
    validate_contract()
    old = {
        "root": predecessor.CONTRACT_ROOT,
        "parent": predecessor.PARENT_CONTRACT,
        "optimization": predecessor.OPTIMIZATION_CONTRACT,
        "contract": predecessor.CONTRACT,
        "validate": predecessor.validate_contract,
        "partial": predecessor.validate_post_fallback_partial,
    }
    predecessor.CONTRACT_ROOT = CONTRACT_ROOT
    predecessor.PARENT_CONTRACT = PARENT_CONTRACT
    predecessor.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    predecessor.CONTRACT = CONTRACT
    predecessor.validate_contract = validate_contract
    predecessor.validate_post_fallback_partial = validate_post_fallback_partial
    try:
        return int(predecessor.main())
    finally:
        predecessor.CONTRACT_ROOT = old["root"]
        predecessor.PARENT_CONTRACT = old["parent"]
        predecessor.OPTIMIZATION_CONTRACT = old["optimization"]
        predecessor.CONTRACT = old["contract"]
        predecessor.validate_contract = old["validate"]
        predecessor.validate_post_fallback_partial = old["partial"]


if __name__ == "__main__":
    raise SystemExit(main())
