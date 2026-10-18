#!/usr/bin/env python3
"""Algorithm C v116 with candidate-local XP instability exclusion."""

from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_algorithm_c_xp_instability_exclusion_v1 as instability
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v25 as predecessor


CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_pg_latest_outer_20260907_v116/"
    "algorithm_c_pg_latest_outer_contract.json"
)
OPTIMIZATION_CONTRACT = ROOT / (
    "config/formal/frozen_mysql_four_scene_algorithm_c_client_primary_20260907_v116/"
    "outer_runtime_optimization.json"
)
COORDINATION_CONTRACT = ROOT / (
    "config/formal/frozen_mysql_cross_project_wave_coordination_20260907_v116/"
    "coordination_contract.json"
)
runtime = predecessor.runtime


def validate_contract() -> dict[str, Any]:
    contract = runtime.alg.load(CONTRACT)
    flow = dict(contract.get("outer_flow") or {})
    fix = dict(contract.get("xp_candidate_local_instability") or {})
    if (
        contract.get("release_version") != "v116-xp-candidate-local-instability"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or flow.get("strict_higher_qps_acceptance") is not True
        or flow.get("two_rejections_before_incumbent_shrink") is not True
        or fix.get("maximum_measurement_seconds") != 900
        or fix.get("invalid_candidate_credit") != 0
        or fix.get("valid_sibling_retained") is not True
        or fix.get("stability_threshold_changed") is not False
    ):
        raise RuntimeError("Algorithm C v116 lifecycle contract differs")
    for name, raw in dict(contract.get("source_bindings") or {}).items():
        row = dict(raw or {})
        path = (ROOT / str(row.get("path") or "")).resolve()
        if not path.is_file() or path.is_symlink() or runtime.alg.sha(path) != row.get("sha256"):
            raise RuntimeError(f"Algorithm C v116 source differs: {name}: {path}")
    return contract


def main() -> int:
    original_classifier = runtime.classify_technical_timeout_batch

    def classify(document, assignments, *, batch_result_path=None):
        technical = original_classifier(
            document, assignments, batch_result_path=batch_result_path,
        )
        return technical or instability.classify(document, assignments)

    old = {
        "contract": predecessor.CONTRACT,
        "optimization": predecessor.OPTIMIZATION_CONTRACT,
        "coordination": predecessor.COORDINATION_CONTRACT,
        "validate": predecessor.validate_contract,
        "classifier": runtime.classify_technical_timeout_batch,
    }
    predecessor.CONTRACT = CONTRACT
    predecessor.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    predecessor.COORDINATION_CONTRACT = COORDINATION_CONTRACT
    predecessor.validate_contract = validate_contract
    runtime.classify_technical_timeout_batch = classify
    try:
        return int(predecessor.main())
    finally:
        runtime.classify_technical_timeout_batch = old["classifier"]
        predecessor.validate_contract = old["validate"]
        predecessor.COORDINATION_CONTRACT = old["coordination"]
        predecessor.OPTIMIZATION_CONTRACT = old["optimization"]
        predecessor.CONTRACT = old["contract"]


if __name__ == "__main__":
    raise SystemExit(main())

