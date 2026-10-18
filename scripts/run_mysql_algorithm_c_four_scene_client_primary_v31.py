#!/usr/bin/env python3
"""v121 Algorithm C: v120 interval search gated by the paper R0 resources."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_c_four_scene_client_primary_v30 as predecessor
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v47 as runtime
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v25 as campaign_controller
from scripts import mysql_algorithm_c_measurement_campaign_root_v3 as campaign_root


CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_paper_baseline_20260920_v121/"
    "algorithm_c_paper_baseline_contract.json"
)
PARENT = predecessor.CONTRACT


def validate_contract() -> dict[str, Any]:
    contract = runtime.alg.load(CONTRACT)
    if (
        contract.get("release_version") != "v121-paper-baseline-one-pp-gate"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("candidate_search_requires_paper_baseline_pass") is not True
        or contract.get("tolerance_percentage_points") != 1.0
        or contract.get("parent_contract", {}).get("sha256") != runtime.alg.sha(PARENT)
    ):
        raise RuntimeError("Algorithm C v121 paper baseline contract differs")
    for name, raw in dict(contract.get("source_bindings") or {}).items():
        path = Path(str(raw.get("path") or ""))
        if not path.is_absolute():
            path = ROOT / path
        if path.is_symlink() or not path.is_file() or runtime.alg.sha(path) != raw.get("sha256"):
            raise RuntimeError(f"Algorithm C v121 source differs: {name}: {path}")
    return contract


def main() -> int:
    # Validate the sealed v120 search semantics before substituting the gate.
    predecessor.validate_contract()
    validate_contract()
    old_runtime = predecessor.runtime
    old_validate = predecessor.validate_contract
    old_contract = predecessor.CONTRACT
    old_campaign_root = campaign_controller.campaign_root.measurement_campaign_root
    predecessor.runtime = runtime
    predecessor.validate_contract = validate_contract
    predecessor.CONTRACT = CONTRACT
    campaign_controller.campaign_root.measurement_campaign_root = (
        campaign_root.measurement_campaign_root
    )
    try:
        return int(predecessor.main())
    finally:
        campaign_controller.campaign_root.measurement_campaign_root = old_campaign_root
        predecessor.CONTRACT = old_contract
        predecessor.validate_contract = old_validate
        predecessor.runtime = old_runtime


if __name__ == "__main__":
    raise SystemExit(main())
