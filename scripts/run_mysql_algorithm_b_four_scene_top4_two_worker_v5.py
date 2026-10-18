#!/usr/bin/env python3
"""Run Top4-B with the frozen 300-second native-XP adapter binding."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import mysql_workload_adapter_v2 as workload_adapter_v2
from scripts import run_mysql_algorithm_b_four_scene_top4_two_worker_v4 as predecessor


CONTRACT_ROOT = ROOT / "config/formal/frozen_mysql_four_scene_top4_algorithm_b_20260826_v48"
CONTRACT = CONTRACT_ROOT / "algorithm_b_top4_contract.json"
COORDINATION = CONTRACT_ROOT / "coordination_contract.json"


def run_hybench_300s(*args: Any, **kwargs: Any) -> dict[str, Any]:
    control = kwargs.get("formal_control")
    if control is not None and int(control.get("graceful_quiescence_seconds", 0)) != 300:
        raise RuntimeError("four-scene Top4-B 300-second XP drain authority differs")
    return workload_adapter_v2.run_hybench(*args, **kwargs)


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
        # The two-worker predecessor replaces the configure hook that used to
        # install mysql_workload_adapter_v2. Rebind it after all predecessor
        # configuration so the frozen 300-second XP drain is accepted.
        alg.run_hybench = run_hybench_300s
        return contract

    def state_document(run_id: str) -> dict[str, Any]:
        document = original_state_document(run_id)
        document["native_xp_300s_adapter_release"] = {
            "source_path": str(ROOT / "mysql_workload_adapter_v2.py"),
            "source_sha256": alg.sha(ROOT / "mysql_workload_adapter_v2.py"),
            "requested_graceful_quiescence_seconds": 300,
            "legacy_adapter_maximum_seconds": 120,
            "workload_composition_changed": False,
            "resource_envelope_changed": False,
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
