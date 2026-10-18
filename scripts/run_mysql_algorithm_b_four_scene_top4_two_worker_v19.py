#!/usr/bin/env python3
"""v62 entry point replacing the obsolete v37 source-hash precheck."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_b_four_scene_top4_two_worker_v18 as predecessor
from scripts import run_mysql_algorithm_b_xp_top4_two_worker_v1 as two_worker_base


CONTRACT_ROOT = (
    ROOT / "config/formal/frozen_mysql_four_scene_top4_algorithm_b_20260827_v62"
)
CONTRACT = CONTRACT_ROOT / "algorithm_b_top4_contract.json"
COORDINATION = CONTRACT_ROOT / "coordination_contract.json"


def install_runtime() -> Any:
    predecessor.CONTRACT_ROOT = CONTRACT_ROOT
    predecessor.CONTRACT = CONTRACT
    predecessor.COORDINATION = COORDINATION
    original_legacy_validate = two_worker_base.predecessor.validate_contract

    def superseded_legacy_validate() -> dict[str, Any]:
        return {
            "status": "SUPERSEDED_BY_V62_COMPLETE_SOURCE_BINDINGS",
            "historical_contract": str(
                ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_b_20260826_v37"
                / "algorithm_b_top4_contract.json"
            ),
            "runtime_or_candidate_validation_bypassed": False,
        }

    two_worker_base.predecessor.validate_contract = superseded_legacy_validate
    try:
        core = predecessor.install_runtime()
    finally:
        two_worker_base.predecessor.validate_contract = original_legacy_validate
    impl = core._impl
    alg = impl["alg"]
    original_state_document = impl["state_document"]

    def state_document(run_id: str) -> dict[str, Any]:
        value = original_state_document(run_id)
        value["legacy_source_hash_precheck_successor"] = {
            "historical_release": "v37",
            "successor_release": "v62",
            "reason": "algorithm_b_core_now_supports_completed_outer_boundary_continuation",
            "complete_v62_source_bindings_required": True,
            "runtime_resource_candidate_and_acceptance_checks_unchanged": True,
        }
        return value

    impl["CONTRACT"] = CONTRACT
    impl["COORDINATION_CONTRACT"] = COORDINATION
    impl["state_document"] = state_document
    return core


def main() -> int:
    core = install_runtime()
    return int(core._implementation_main())


if __name__ == "__main__":
    raise SystemExit(main())
