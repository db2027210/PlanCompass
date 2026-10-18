#!/usr/bin/env python3
"""v59 runtime retaining the reset caller's 120-second Docker start timeout."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import reset_mysql_xp_data_planes_four_worker_v3 as reset_v3
from scripts import run_mysql_algorithm_b_four_scene_top4_two_worker_v14 as predecessor

CONTRACT_ROOT = ROOT / "config/formal/frozen_mysql_four_scene_top4_algorithm_b_20260826_v59"
CONTRACT = CONTRACT_ROOT / "algorithm_b_top4_contract.json"
COORDINATION = CONTRACT_ROOT / "coordination_contract.json"


def install_runtime() -> Any:
    predecessor.CONTRACT_ROOT = CONTRACT_ROOT
    predecessor.CONTRACT = CONTRACT
    predecessor.COORDINATION = COORDINATION
    core = predecessor.install_runtime()
    impl = core._impl
    alg = impl["alg"]
    original_configure = impl["configure_runtime"]
    original_state_document = impl["state_document"]

    def configure_runtime() -> Mapping[str, Any]:
        contract = original_configure()
        reset_v3.install()
        alg.reset = reset_v3
        impl["runtime"].alg.reset = reset_v3
        return contract

    def state_document(run_id: str) -> dict[str, Any]:
        value = original_state_document(run_id)
        value["docker_start_timeout_successor"] = {
            "source_path": str(ROOT / "scripts/reset_mysql_xp_data_planes_four_worker_v3.py"),
            "source_sha256": alg.sha(ROOT / "scripts/reset_mysql_xp_data_planes_four_worker_v3.py"),
            "docker_start_serialization_preserved": True,
            "caller_timeout_seconds_preserved": 120,
            "predecessor_forced_timeout_seconds": 30,
            "container_running_readback_after_timeout_preserved": True,
            "candidate_or_acceptance_logic_changed": False,
        }
        return value

    impl["configure_runtime"] = configure_runtime
    impl["state_document"] = state_document
    return core


def main() -> int:
    core = install_runtime()
    return int(core._implementation_main())


if __name__ == "__main__":
    raise SystemExit(main())
