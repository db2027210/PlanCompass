#!/usr/bin/env python3
"""Run four-worker XP Algorithm C with mixed-wave cap isolation v77."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_algorithm_c_mixed_stability_cap_isolation_v1 as cap_isolation
from scripts import run_mysql_algorithm_c_async_scene_v76 as predecessor


CONTRACT_ROOT = (
    ROOT / "config/formal/frozen_mysql_four_scene_algorithm_c_cpu_a_slots_20260828_v29"
)
PARENT_CONTRACT = CONTRACT_ROOT / "algorithm_c_async_contract.json"
OPTIMIZATION_CONTRACT = CONTRACT_ROOT / "outer_runtime_optimization.json"
CONTRACT = CONTRACT_ROOT / "global_monotonic_incumbent_contract.json"


def mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} object required")
    return dict(value)


def validate_contract() -> dict[str, Any]:
    alg = predecessor.predecessor.predecessor.alg
    parent = alg.load(PARENT_CONTRACT)
    optimization = alg.load(OPTIMIZATION_CONTRACT)
    contract = alg.load(CONTRACT)
    if (
        parent.get("release_version") != "v73"
        or parent.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or parent.get("formal_scope") != "mysql_only"
        or parent.get("cross_engine_comparison_prohibited") is not True
        or parent.get("candidate_batches") != [[1, 2, 3, 4], [5]]
        or parent.get("four_scene_wave_barrier") is not False
        or parent.get("sibling_scene_wait") is not False
        or parent.get("lease_mode") != "none"
        or optimization.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("schema_version")
        != "mysql_algorithm_c_global_monotonic_incumbent_contract_v1"
        or contract.get("release_version") != "v77"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("parent_contract", {}).get("sha256")
        != alg.sha(PARENT_CONTRACT)
        or contract.get("stability_cap_isolation", {}).get(
            "valid_sibling_measurements_retained"
        ) is not True
        or contract.get("stability_cap_isolation", {}).get(
            "failed_lane_only_excluded"
        ) is not True
    ):
        raise RuntimeError("Algorithm C v77 mixed-wave contract differs")
    for document, label in ((parent, "parent"), (contract, "successor")):
        for name, raw in mapping(
            document.get("source_bindings"), f"{label} bindings"
        ).items():
            row = mapping(raw, f"{label} {name}")
            path = (ROOT / str(row.get("path") or "")).resolve()
            if (
                not path.is_file()
                or path.is_symlink()
                or alg.sha(path) != row.get("sha256")
            ):
                raise RuntimeError(f"Algorithm C v77 source binding differs: {name}")
    return {"parent": parent, "optimization": optimization, "successor": contract}


def main() -> int:
    validate_contract()
    boundary = predecessor.predecessor.predecessor.runtime
    old = {
        "contract_root": predecessor.CONTRACT_ROOT,
        "parent": predecessor.PARENT_CONTRACT,
        "optimization": predecessor.OPTIMIZATION_CONTRACT,
        "contract": predecessor.CONTRACT,
        "validate": predecessor.validate_contract,
        "classifier": boundary.classify_technical_timeout_batch,
    }

    def classify(document, assignments, *, batch_result_path=None):
        return cap_isolation.classify(
            document,
            assignments,
            batch_result_path=batch_result_path,
            predecessor=old["classifier"],
        )

    predecessor.CONTRACT_ROOT = CONTRACT_ROOT
    predecessor.PARENT_CONTRACT = PARENT_CONTRACT
    predecessor.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    predecessor.CONTRACT = CONTRACT
    predecessor.validate_contract = validate_contract
    boundary.classify_technical_timeout_batch = classify
    try:
        return int(predecessor.main())
    finally:
        predecessor.CONTRACT_ROOT = old["contract_root"]
        predecessor.PARENT_CONTRACT = old["parent"]
        predecessor.OPTIMIZATION_CONTRACT = old["optimization"]
        predecessor.CONTRACT = old["contract"]
        predecessor.validate_contract = old["validate"]
        boundary.classify_technical_timeout_batch = old["classifier"]


if __name__ == "__main__":
    raise SystemExit(main())
