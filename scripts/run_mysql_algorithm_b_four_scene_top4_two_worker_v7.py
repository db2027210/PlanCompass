#!/usr/bin/env python3
"""Run Top4-B with v4 XP control and storage-scoped interference gating."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_v9 as gate_v9
from scripts import run_mysql_algorithm_b_four_scene_top4_two_worker_v6 as predecessor


CONTRACT_ROOT = ROOT / "config/formal/frozen_mysql_four_scene_top4_algorithm_b_20260826_v50"
CONTRACT = CONTRACT_ROOT / "algorithm_b_top4_contract.json"
COORDINATION = CONTRACT_ROOT / "coordination_contract.json"


def install_runtime() -> Any:
    predecessor.CONTRACT_ROOT = CONTRACT_ROOT
    predecessor.CONTRACT = CONTRACT
    predecessor.COORDINATION = COORDINATION
    core = predecessor.install_runtime()
    impl = core._impl
    alg = impl["alg"]
    original_configure_runtime = impl["configure_runtime"]
    original_state_document = impl["state_document"]

    def configure_runtime() -> Mapping[str, Any]:
        contract = original_configure_runtime()
        alg.r0mod.dynamic_gate = gate_v9
        impl["no_lease_runtime"].coordination = gate_v9
        return contract

    def state_document(run_id: str) -> dict[str, Any]:
        document = original_state_document(run_id)
        document["storage_scoped_journal_gate_release"] = {
            "source_path": str(ROOT / "scripts/mysql_cross_project_wave_coordination_v9.py"),
            "source_sha256": alg.sha(ROOT / "scripts/mysql_cross_project_wave_coordination_v9.py"),
            "disk1_disk2_physical_device_disjointness_required": True,
            "persistent_unknown_d_state_remains_fail_closed": True,
            "cross_scene_completion_dependency": False,
        }
        return document

    impl["CONTRACT"] = CONTRACT
    impl["COORDINATION_CONTRACT"] = COORDINATION
    impl["configure_runtime"] = configure_runtime
    impl["state_document"] = state_document
    return core


def main() -> int:
    core = install_runtime()
    return int(core._implementation_main())


if __name__ == "__main__":
    raise SystemExit(main())
