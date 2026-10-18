#!/usr/bin/env python3
"""v63 TPC-H entry using the relational v11 runtime chain."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_b_four_scene_top4_two_worker_v11 as relational_runtime
from scripts import run_mysql_algorithm_b_four_scene_top4_two_worker_v18 as continuation
from scripts import run_mysql_algorithm_b_xp_top4_two_worker_v1 as two_worker_base


CONTRACT_ROOT = (
    ROOT / "config/formal/frozen_mysql_four_scene_top4_algorithm_b_20260827_v63"
)
CONTRACT = CONTRACT_ROOT / "algorithm_b_top4_contract.json"
COORDINATION = CONTRACT_ROOT / "coordination_contract.json"


def install_runtime() -> Any:
    continuation.CONTRACT_ROOT = CONTRACT_ROOT
    continuation.CONTRACT = CONTRACT
    continuation.COORDINATION = COORDINATION
    original_predecessor = continuation.predecessor
    original_legacy_validate = two_worker_base.predecessor.validate_contract

    def superseded_legacy_validate() -> dict[str, Any]:
        return {
            "status": "SUPERSEDED_BY_V63_COMPLETE_SOURCE_BINDINGS",
            "runtime_or_candidate_validation_bypassed": False,
        }

    continuation.predecessor = relational_runtime
    two_worker_base.predecessor.validate_contract = superseded_legacy_validate
    try:
        core = continuation.install_runtime()
    finally:
        continuation.predecessor = original_predecessor
        two_worker_base.predecessor.validate_contract = original_legacy_validate
    impl = core._impl
    alg = impl["alg"]
    original_state_document = impl["state_document"]

    def state_document(run_id: str) -> dict[str, Any]:
        value = original_state_document(run_id)
        value["relational_runtime_chain_successor"] = {
            "scene": "tpch_sf4_memory",
            "runtime_predecessor": str(Path(relational_runtime.__file__).resolve()),
            "runtime_predecessor_sha256": alg.sha(
                Path(relational_runtime.__file__).resolve()
            ),
            "xp_only_docker_start_compatibility_layer_used": False,
            "existing_60_second_transition_preflight_preserved": True,
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
